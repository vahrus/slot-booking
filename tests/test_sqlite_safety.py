"""SQLite locking, minute precision, and identifier behavior for new databases."""

import threading
from collections.abc import Generator
from datetime import date, time
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.testclient import TestClient
from sqlalchemy import func, select, text
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session, sessionmaker

from app.database.database import (
    Base,
    create_sqlite_engine,
    get_db,
    is_sqlite_lock_error,
)
from app.database.models import Booking, Slot
from app.schemas.booking import BookingCreate
from app.schemas.slot import SlotCreate
from app.services.bookings import (
    BookingConflictError,
    SlotNotAvailableError,
    cancel_booking,
    create_booking,
)
from app.services.slots import SlotAlreadyExistsError, create_slot


FUTURE = date(2026, 12, 2)


def test_slot_time_truncates_seconds_and_microseconds() -> None:
    created = SlotCreate(
        date=FUTURE,
        time=time(9, 15, 59, 999_999),
    )

    assert created.time == time(9, 15)
    assert created.time.second == 0
    assert created.time.microsecond == 0


def test_same_minute_with_different_seconds_is_one_slot(
    tmp_path: Path,
) -> None:
    engine = create_sqlite_engine(f"sqlite:///{tmp_path / 'minutes.db'}")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)

    try:
        with factory() as session:
            first = create_slot(
                session,
                SlotCreate(date=FUTURE, time=time(10, 0, 45)),
            )
            assert first.time == time(10, 0)

            with pytest.raises(SlotAlreadyExistsError):
                create_slot(
                    session,
                    SlotCreate(date=FUTURE, time=time(10, 0, 1, 250_000)),
                )

            stored = session.scalars(select(Slot)).all()
            assert len(stored) == 1
            assert stored[0].time == time(10, 0)
    finally:
        engine.dispose()


def test_form_seconds_are_stored_as_the_whole_minute(tmp_path: Path) -> None:
    engine = create_sqlite_engine(f"sqlite:///{tmp_path / 'form-minutes.db'}")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)

    def override_get_db() -> Generator[Session, None, None]:
        with factory() as session:
            yield session

    application = FastAPI()
    static_dir = Path(__file__).resolve().parents[1] / "app" / "static"
    application.mount("/static", StaticFiles(directory=static_dir), name="static")
    from app.routers import admin

    application.include_router(admin.router)
    application.dependency_overrides[get_db] = override_get_db

    try:
        with TestClient(application) as client:
            created = client.post(
                "/admin/slots",
                data={"date": FUTURE.isoformat(), "time": "15:30:45"},
                follow_redirects=False,
            )
            assert created.status_code == 303

            duplicate = client.post(
                "/admin/slots",
                data={"date": FUTURE.isoformat(), "time": "15:30:01"},
                follow_redirects=False,
            )
            assert duplicate.status_code == 303
            assert "error=duplicate" in duplicate.headers["location"]

        with factory() as session:
            slots = session.scalars(select(Slot)).all()
            assert [(slot.time, slot.date) for slot in slots] == [(time(15, 30), FUTURE)]
    finally:
        engine.dispose()


def test_existing_second_precision_row_is_not_rewritten(tmp_path: Path) -> None:
    engine = create_sqlite_engine(f"sqlite:///{tmp_path / 'legacy-time.db'}")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)

    try:
        with factory() as session:
            existing = Slot(date=FUTURE, time=time(8, 0, 45), status="free")
            session.add(existing)
            session.commit()
            existing_id = existing.id

            created = create_slot(
                session,
                SlotCreate(date=FUTURE, time=time(8, 30, 10)),
            )

            preserved = session.get(Slot, existing_id)
            assert preserved is not None
            assert preserved.time == time(8, 0, 45)
            assert created.time == time(8, 30)
            assert session.scalar(select(func.count()).select_from(Slot)) == 2
    finally:
        engine.dispose()


def test_sqlite_lock_messages_are_recognized() -> None:
    locked = OperationalError("BEGIN IMMEDIATE", {}, Exception("database is locked"))
    table_locked = OperationalError(
        "UPDATE slots",
        {},
        Exception("database table is locked"),
    )
    unrelated = OperationalError("SELECT", {}, Exception("disk I/O error"))

    assert is_sqlite_lock_error(locked)
    assert is_sqlite_lock_error(table_locked)
    assert not is_sqlite_lock_error(unrelated)


def test_database_locked_on_commit_rolls_back_and_returns_409(tmp_path: Path) -> None:
    engine = create_sqlite_engine(f"sqlite:///{tmp_path / 'locked.db'}")
    Base.metadata.create_all(engine)
    setup = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)

    class LockedCommitSession(Session):
        """Fail the booking commit after the INSERT has been flushed."""

        def commit(self) -> None:
            if any(isinstance(obj, Booking) for obj in self.new):
                self.flush()
                raise OperationalError(
                    "COMMIT",
                    {},
                    Exception("database is locked"),
                )
            super().commit()

    locked = sessionmaker(
        bind=engine,
        autoflush=False,
        expire_on_commit=False,
        class_=LockedCommitSession,
    )

    with setup() as session:
        slot_id = create_slot(
            session,
            SlotCreate(date=FUTURE, time=time(14, 0)),
        ).id

    def override_get_db() -> Generator[Session, None, None]:
        with locked() as session:
            yield session

    application = FastAPI()
    static_dir = Path(__file__).resolve().parents[1] / "app" / "static"
    application.mount("/static", StaticFiles(directory=static_dir), name="static")
    from app.routers import public

    application.include_router(public.router)
    application.dependency_overrides[get_db] = override_get_db

    try:
        with TestClient(application) as client:
            response = client.post(
                f"/booking/{slot_id}",
                data={"client_name": "Анна", "client_contact": "anna@example.com"},
            )

        assert response.status_code == 409
        assert "Это время уже занято" in response.text
        with setup() as session:
            assert session.scalar(select(func.count()).select_from(Booking)) == 0
            saved = session.get(Slot, slot_id)
            assert saved is not None
            assert saved.status == "free"
    finally:
        engine.dispose()


def test_concurrent_bookings_create_a_single_row(tmp_path: Path) -> None:
    engine = create_sqlite_engine(f"sqlite:///{tmp_path / 'race.db'}")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    with factory() as session:
        slot_id = create_slot(
            session,
            SlotCreate(date=FUTURE, time=time(16, 0)),
        ).id

    barrier = threading.Barrier(2)
    results: list[tuple[str, object]] = []
    results_lock = threading.Lock()

    def attempt() -> None:
        try:
            barrier.wait(timeout=5)
            with factory() as session:
                booking = create_booking(
                    session,
                    slot_id,
                    BookingCreate(client_name="Анна", client_contact="anna@example.com"),
                )
                outcome: tuple[str, object] = ("ok", booking.id)
        except (SlotNotAvailableError, BookingConflictError) as exc:
            outcome = ("error", type(exc).__name__)
        except Exception as exc:
            outcome = ("unexpected", f"{type(exc).__name__}: {exc}")

        with results_lock:
            results.append(outcome)

    threads = [threading.Thread(target=attempt) for _ in range(2)]
    try:
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=15)

        assert [thread.is_alive() for thread in threads] == [False, False]
        successes = [item for item in results if item[0] == "ok"]
        conflicts = [item for item in results if item[0] == "error"]
        unexpected = [item for item in results if item[0] == "unexpected"]
        assert unexpected == []
        assert len(successes) == 1
        assert len(conflicts) == 1
        assert conflicts[0][1] in {"SlotNotAvailableError", "BookingConflictError"}

        with factory() as session:
            assert session.scalar(select(func.count()).select_from(Booking)) == 1
            saved = session.get(Slot, slot_id)
            assert saved is not None
            assert saved.status == "booked"
    finally:
        engine.dispose()


def test_new_database_autoincrement_does_not_reuse_ids(tmp_path: Path) -> None:
    engine = create_sqlite_engine(f"sqlite:///{tmp_path / 'fresh.db'}")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)

    try:
        with engine.connect() as connection:
            booking_sql = connection.execute(
                text("SELECT sql FROM sqlite_master WHERE name = 'bookings'")
            ).scalar_one()
            slot_sql = connection.execute(
                text("SELECT sql FROM sqlite_master WHERE name = 'slots'")
            ).scalar_one()
        assert "AUTOINCREMENT" in booking_sql
        assert "AUTOINCREMENT" in slot_sql

        with factory() as session:
            slot = create_slot(
                session,
                SlotCreate(date=FUTURE, time=time(12, 0)),
            )
            first = create_booking(
                session,
                slot.id,
                BookingCreate(client_name="Анна", client_contact="anna@example.com"),
            )
            first_id = first.id
            cancel_booking(session, first.id)
            second = create_booking(
                session,
                slot.id,
                BookingCreate(client_name="Иван", client_contact="ivan@example.com"),
            )

            assert second.id != first_id
            assert session.get(Booking, first_id) is None
            assert session.scalar(select(func.count()).select_from(Booking)) == 1
    finally:
        engine.dispose()


def test_create_all_does_not_migrate_or_rewrite_legacy_rows(tmp_path: Path) -> None:
    engine = create_sqlite_engine(f"sqlite:///{tmp_path / 'legacy.db'}")
    with engine.begin() as connection:
        connection.execute(
            text(
                """
                CREATE TABLE slots (
                    id INTEGER NOT NULL,
                    date DATE NOT NULL,
                    time TIME NOT NULL,
                    status VARCHAR(20) DEFAULT 'free' NOT NULL,
                    PRIMARY KEY (id),
                    CONSTRAINT uq_slots_date_time UNIQUE (date, time)
                )
                """
            )
        )
        connection.execute(
            text(
                """
                CREATE TABLE bookings (
                    id INTEGER NOT NULL,
                    slot_id INTEGER NOT NULL,
                    client_name VARCHAR(200) NOT NULL,
                    client_contact VARCHAR(200) NOT NULL,
                    created_at DATETIME DEFAULT CURRENT_TIMESTAMP NOT NULL,
                    PRIMARY KEY (id),
                    CONSTRAINT uq_bookings_slot_id UNIQUE (slot_id),
                    FOREIGN KEY(slot_id) REFERENCES slots (id)
                )
                """
            )
        )
        connection.execute(
            text(
                "INSERT INTO slots (id, date, time, status) "
                "VALUES (7, '2026-12-04', '10:00:45', 'free')"
            )
        )
        connection.execute(
            text(
                "INSERT INTO bookings (id, slot_id, client_name, client_contact) "
                "VALUES (4, 7, 'Анна', 'anna@example.com')"
            )
        )

    try:
        Base.metadata.create_all(bind=engine)
        with engine.connect() as connection:
            booking_sql = connection.execute(
                text("SELECT sql FROM sqlite_master WHERE name = 'bookings'")
            ).scalar_one()
            slot_count = connection.execute(text("SELECT COUNT(*) FROM slots")).scalar_one()
            booking_count = connection.execute(
                text("SELECT COUNT(*) FROM bookings")
            ).scalar_one()
            preserved_time = connection.execute(
                text("SELECT time FROM slots WHERE id = 7")
            ).scalar_one()
            preserved_contact = connection.execute(
                text("SELECT client_contact FROM bookings WHERE id = 4")
            ).scalar_one()

        assert "AUTOINCREMENT" not in booking_sql
        assert slot_count == 1
        assert booking_count == 1
        assert preserved_time == "10:00:45"
        assert preserved_contact == "anna@example.com"

        with engine.connect() as connection:
            foreign_keys = connection.execute(text("PRAGMA foreign_keys")).scalar_one()
            journal_mode = connection.execute(text("PRAGMA journal_mode")).scalar_one()
            busy_timeout = connection.execute(text("PRAGMA busy_timeout")).scalar_one()
        assert foreign_keys == 1
        assert journal_mode == "wal"
        assert busy_timeout == 5000
    finally:
        engine.dispose()
