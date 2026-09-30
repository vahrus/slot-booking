import datetime
from collections.abc import Iterator
from pathlib import Path

import pytest
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from app.database.database import Base, create_sqlite_engine
from app.database.models import Booking, Slot


@pytest.fixture
def session_factory(
    tmp_path: Path,
) -> Iterator[sessionmaker[Session]]:
    database_url = f"sqlite:///{tmp_path / 'test.db'}"
    test_engine = create_sqlite_engine(database_url)
    Base.metadata.create_all(bind=test_engine)
    factory = sessionmaker(
        bind=test_engine,
        autoflush=False,
        expire_on_commit=False,
    )

    yield factory

    test_engine.dispose()


def make_slot() -> Slot:
    return Slot(
        date=datetime.date(2026, 10, 10),
        time=datetime.time(10, 0),
    )


def test_create_slot(session_factory: sessionmaker[Session]) -> None:
    with session_factory() as session:
        slot = make_slot()
        session.add(slot)
        session.commit()
        session.refresh(slot)

        assert slot.id is not None
        assert slot.date == datetime.date(2026, 10, 10)
        assert isinstance(slot.date, datetime.date)
        assert slot.time == datetime.time(10, 0)
        assert isinstance(slot.time, datetime.time)
        assert slot.status == "free"


def test_duplicate_slot_is_rejected(
    session_factory: sessionmaker[Session],
) -> None:
    with session_factory() as session:
        session.add(make_slot())
        session.commit()
        session.add(make_slot())

        with pytest.raises(IntegrityError):
            session.commit()

        session.rollback()


def test_create_booking(session_factory: sessionmaker[Session]) -> None:
    with session_factory() as session:
        slot = make_slot()
        session.add(slot)
        session.commit()

        booking = Booking(
            slot_id=slot.id,
            client_name="Анна",
            client_contact="anna@example.com",
        )
        session.add(booking)
        session.commit()
        session.refresh(booking)

        assert booking.id is not None
        assert booking.slot_id == slot.id
        assert booking.client_name == "Анна"
        assert booking.client_contact == "anna@example.com"
        assert booking.created_at is not None


def test_bidirectional_relationship(
    session_factory: sessionmaker[Session],
) -> None:
    with session_factory() as session:
        slot = make_slot()
        booking = Booking(
            slot=slot,
            client_name="Иван",
            client_contact="+7 900 000-00-00",
        )
        session.add_all([slot, booking])
        session.commit()

        assert booking.slot is slot
        assert slot.booking is booking


def test_second_booking_for_slot_is_rejected(
    session_factory: sessionmaker[Session],
) -> None:
    with session_factory() as session:
        slot = make_slot()
        session.add(slot)
        session.commit()

        session.add(
            Booking(
                slot_id=slot.id,
                client_name="Первый клиент",
                client_contact="first@example.com",
            )
        )
        session.commit()
        session.add(
            Booking(
                slot_id=slot.id,
                client_name="Второй клиент",
                client_contact="second@example.com",
            )
        )

        with pytest.raises(IntegrityError):
            session.commit()

        session.rollback()


def test_booking_requires_existing_slot(
    session_factory: sessionmaker[Session],
) -> None:
    with session_factory() as session:
        session.add(
            Booking(
                slot_id=999_999,
                client_name="Анна",
                client_contact="anna@example.com",
            )
        )

        with pytest.raises(IntegrityError):
            session.commit()

        session.rollback()


def test_slot_persists_between_sessions(
    session_factory: sessionmaker[Session],
) -> None:
    with session_factory() as first_session:
        slot = make_slot()
        first_session.add(slot)
        first_session.commit()
        slot_id = slot.id

    with session_factory() as second_session:
        saved_slot = second_session.get(Slot, slot_id)

        assert saved_slot is not None
        assert saved_slot.date == datetime.date(2026, 10, 10)
        assert saved_slot.time == datetime.time(10, 0)
        assert saved_slot.status == "free"
