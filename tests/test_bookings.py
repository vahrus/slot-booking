import datetime
from collections.abc import Generator, Iterator
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.testclient import TestClient
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

from app.database.database import Base, create_sqlite_engine, get_db
from app.database.models import Booking, Slot
from app.routers import admin, public
from app.schemas.booking import BookingCreate
from app.schemas.slot import SlotCreate
from app.services.bookings import (
    BookingCancellationError,
    BookingConflictError,
    BookingNotFoundError,
    SlotNotAvailableError,
    SlotNotFoundError,
    cancel_booking,
    create_booking,
)
from app.services.slots import create_slot


@pytest.fixture
def session_factory(
    tmp_path: Path,
) -> Iterator[sessionmaker[Session]]:
    database_url = f"sqlite:///{tmp_path / 'bookings-test.db'}"
    test_engine = create_sqlite_engine(database_url)
    Base.metadata.create_all(bind=test_engine)
    factory = sessionmaker(
        bind=test_engine,
        autoflush=False,
        expire_on_commit=False,
    )

    yield factory

    test_engine.dispose()


@pytest.fixture
def client(
    session_factory: sessionmaker[Session],
) -> Iterator[TestClient]:
    def override_get_db() -> Generator[Session, None, None]:
        with session_factory() as session:
            yield session

    test_app = FastAPI()
    static_dir = Path(__file__).resolve().parents[1] / "app" / "static"
    test_app.mount("/static", StaticFiles(directory=static_dir), name="static")
    test_app.include_router(public.router)
    test_app.include_router(admin.router)
    test_app.dependency_overrides[get_db] = override_get_db

    with TestClient(test_app) as test_client:
        yield test_client


def make_slot(
    session: Session,
    *,
    time_value: datetime.time = datetime.time(10, 0),
) -> Slot:
    return create_slot(
        session,
        SlotCreate(date=datetime.date(2026, 10, 10), time=time_value),
    )


def booking_data(
    name: str = "Анна Иванова",
    contact: str = "+7 999 123-45-67",
) -> BookingCreate:
    return BookingCreate(client_name=name, client_contact=contact)


def test_create_booking_success(
    session_factory: sessionmaker[Session],
) -> None:
    with session_factory() as session:
        slot = make_slot(session)
        booking = create_booking(session, slot.id, booking_data())

        assert booking.id is not None
        assert booking.slot_id == slot.id
        assert booking.client_name == "Анна Иванова"
        assert booking.client_contact == "+7 999 123-45-67"
        assert booking.created_at is not None
        assert session.get(Slot, slot.id).status == "booked"


def test_booking_normalizes_whitespace(
    session_factory: sessionmaker[Session],
) -> None:
    with session_factory() as session:
        slot = make_slot(session)
        booking = create_booking(
            session,
            slot.id,
            booking_data(
                name="  Анна Иванова  ",
                contact="  +7 999 123-45-67  ",
            ),
        )

        assert booking.client_name == "Анна Иванова"
        assert booking.client_contact == "+7 999 123-45-67"


def test_booking_nonexistent_slot(
    session_factory: sessionmaker[Session],
) -> None:
    with session_factory() as session:
        with pytest.raises(SlotNotFoundError):
            create_booking(session, 999_999, booking_data())

        assert session.scalar(select(func.count()).select_from(Booking)) == 0
        later_slot = make_slot(session)
        assert later_slot.id is not None


def test_booking_rejects_booked_slot(
    session_factory: sessionmaker[Session],
) -> None:
    with session_factory() as session:
        slot = make_slot(session)
        slot.status = "booked"
        session.commit()

        with pytest.raises(SlotNotAvailableError):
            create_booking(session, slot.id, booking_data())

        assert session.scalar(select(func.count()).select_from(Booking)) == 0


def test_second_booking_is_blocked(
    session_factory: sessionmaker[Session],
) -> None:
    with session_factory() as session:
        slot = make_slot(session)
        first_booking = create_booking(session, slot.id, booking_data())

        with pytest.raises(SlotNotAvailableError):
            create_booking(
                session,
                slot.id,
                booking_data(name="Иван Петров", contact="ivan@example.com"),
            )

        assert session.scalar(select(func.count()).select_from(Booking)) == 1
        saved_booking = session.get(Booking, first_booking.id)
        assert saved_booking is not None
        assert saved_booking.client_name == "Анна Иванова"
        assert session.get(Slot, slot.id).status == "booked"
        later_slot = make_slot(session, time_value=datetime.time(11, 0))
        assert later_slot.id is not None


def test_booking_constraint_conflict_rolls_back(
    session_factory: sessionmaker[Session],
) -> None:
    with session_factory() as session:
        slot = make_slot(session)
        session.add(
            Booking(
                slot_id=slot.id,
                client_name="Первый клиент",
                client_contact="first@example.com",
            )
        )
        session.commit()

        with pytest.raises(BookingConflictError):
            create_booking(session, slot.id, booking_data())

        assert session.scalar(select(func.count()).select_from(Booking)) == 1
        assert session.get(Slot, slot.id).status == "free"


def test_cancel_booking_success(
    session_factory: sessionmaker[Session],
) -> None:
    with session_factory() as session:
        slot = make_slot(session)
        booking = create_booking(session, slot.id, booking_data())
        booking_id = booking.id
        slot_id = slot.id

        cancelled_slot = cancel_booking(session, booking_id)

        assert cancelled_slot.id == slot_id
        assert cancelled_slot.status == "free"
        assert session.get(Booking, booking_id) is None
        saved_slot = session.get(Slot, slot_id)
        assert saved_slot is not None
        assert saved_slot.status == "free"


def test_cancel_booking_keeps_slot(
    session_factory: sessionmaker[Session],
) -> None:
    with session_factory() as session:
        slot = make_slot(session)
        booking = create_booking(session, slot.id, booking_data())
        slot_id = slot.id

        cancel_booking(session, booking.id)

        assert session.get(Slot, slot_id) is not None
        assert session.scalar(select(func.count()).select_from(Slot)) == 1


def test_cancel_nonexistent_booking_is_controlled(
    session_factory: sessionmaker[Session],
) -> None:
    with session_factory() as session:
        slot = make_slot(session)

        with pytest.raises(BookingNotFoundError):
            cancel_booking(session, 999_999)

        saved_slot = session.get(Slot, slot.id)
        assert saved_slot is not None
        assert saved_slot.status == "free"
        later_slot = make_slot(session, time_value=datetime.time(11, 0))
        assert later_slot.id is not None


def test_rebooking_after_cancellation(
    session_factory: sessionmaker[Session],
) -> None:
    with session_factory() as session:
        slot = make_slot(session)
        first_booking = create_booking(session, slot.id, booking_data())

        cancel_booking(session, first_booking.id)
        second_booking = create_booking(
            session,
            slot.id,
            booking_data(name="Иван Петров", contact="ivan@example.com"),
        )

        assert second_booking.slot_id == slot.id
        assert second_booking.client_name == "Иван Петров"
        assert session.get(Slot, slot.id).status == "booked"
        assert session.scalar(select(func.count()).select_from(Booking)) == 1


class CommitFailureSession(Session):
    """Session that fails after flushing so rollback can be verified."""

    def commit(self) -> None:
        self.flush()
        raise SQLAlchemyError("forced commit failure")


def test_cancel_booking_rolls_back_partial_changes(
    session_factory: sessionmaker[Session],
) -> None:
    with session_factory() as session:
        slot = make_slot(session)
        booking = create_booking(session, slot.id, booking_data())
        slot_id = slot.id
        booking_id = booking.id
        bind = session.get_bind()

    failing_factory = sessionmaker(
        bind=bind,
        autoflush=False,
        expire_on_commit=False,
        class_=CommitFailureSession,
    )
    with failing_factory() as session:
        with pytest.raises(BookingCancellationError):
            cancel_booking(session, booking_id)

    with session_factory() as session:
        saved_booking = session.get(Booking, booking_id)
        saved_slot = session.get(Slot, slot_id)
        assert saved_booking is not None
        assert saved_slot is not None
        assert saved_slot.status == "booked"


def test_get_booking_form(client: TestClient, session_factory: sessionmaker[Session]) -> None:
    with session_factory() as session:
        slot = make_slot(session)
        slot_id = slot.id

    response = client.get(f"/booking/{slot_id}")

    assert response.status_code == 200
    assert "10 октября 2026" in response.text
    assert "10:00" in response.text
    assert "Подтвердить запись" in response.text
    assert 'name="client_name"' in response.text
    assert 'name="client_contact"' in response.text


def test_get_nonexistent_booking_slot(client: TestClient) -> None:
    response = client.get("/booking/999999")

    assert response.status_code == 404
    assert "<html" in response.text.lower()
    assert "не найден" in response.text.lower()


def test_get_booked_slot_form_is_blocked(
    client: TestClient,
    session_factory: sessionmaker[Session],
) -> None:
    with session_factory() as session:
        slot = make_slot(session)
        slot.status = "booked"
        session.commit()
        slot_id = slot.id

    response = client.get(f"/booking/{slot_id}")

    assert response.status_code == 409
    assert "Это время уже занято. Выберите другой слот." in response.text
    assert "Подтвердить запись" not in response.text


def test_post_successful_booking(
    client: TestClient,
    session_factory: sessionmaker[Session],
) -> None:
    with session_factory() as session:
        slot = make_slot(session)
        slot_id = slot.id

    response = client.post(
        f"/booking/{slot_id}",
        data={
            "client_name": "Анна Иванова",
            "client_contact": "+7 999 123-45-67",
        },
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert response.headers["location"].endswith("/success")

    success = client.get(response.headers["location"])
    assert success.status_code == 200
    assert "Запись подтверждена" in success.text
    assert "Анна Иванова" in success.text

    with session_factory() as session:
        booking = session.scalar(select(Booking))
        slot = session.get(Slot, slot_id)
        assert booking is not None
        assert slot is not None
        assert slot.status == "booked"


def test_post_empty_name_is_rejected(
    client: TestClient,
    session_factory: sessionmaker[Session],
) -> None:
    with session_factory() as session:
        slot = make_slot(session)
        slot_id = slot.id

    response = client.post(
        f"/booking/{slot_id}",
        data={"client_name": "", "client_contact": "+7 999 123-45-67"},
    )

    assert response.status_code == 400
    assert "Введите имя." in response.text

    with session_factory() as session:
        assert session.scalar(select(func.count()).select_from(Booking)) == 0
        assert session.get(Slot, slot_id).status == "free"


def test_post_whitespace_name_is_rejected(
    client: TestClient,
    session_factory: sessionmaker[Session],
) -> None:
    with session_factory() as session:
        slot = make_slot(session)
        slot_id = slot.id

    response = client.post(
        f"/booking/{slot_id}",
        data={"client_name": "     ", "client_contact": "+7 999 123-45-67"},
    )

    assert response.status_code == 400
    assert "Введите имя." in response.text

    with session_factory() as session:
        assert session.scalar(select(func.count()).select_from(Booking)) == 0
        assert session.get(Slot, slot_id).status == "free"


def test_post_empty_contact_is_rejected(
    client: TestClient,
    session_factory: sessionmaker[Session],
) -> None:
    with session_factory() as session:
        slot = make_slot(session)
        slot_id = slot.id

    response = client.post(
        f"/booking/{slot_id}",
        data={"client_name": "Анна", "client_contact": ""},
    )

    assert response.status_code == 400
    assert "Укажите контакт для связи." in response.text

    with session_factory() as session:
        assert session.scalar(select(func.count()).select_from(Booking)) == 0
        assert session.get(Slot, slot_id).status == "free"


def test_repeated_post_is_rejected(
    client: TestClient,
    session_factory: sessionmaker[Session],
) -> None:
    with session_factory() as session:
        slot = make_slot(session)
        slot_id = slot.id

    form = {
        "client_name": "Анна Иванова",
        "client_contact": "+7 999 123-45-67",
    }
    client.post(f"/booking/{slot_id}", data=form)

    response = client.post(f"/booking/{slot_id}", data=form)

    assert response.status_code == 409
    assert "Это время уже занято. Выберите другой слот." in response.text

    with session_factory() as session:
        assert session.scalar(select(func.count()).select_from(Booking)) == 1
        slot = session.get(Slot, slot_id)
        assert slot is not None
        assert slot.status == "booked"


def test_success_page_reads_booking(
    client: TestClient,
    session_factory: sessionmaker[Session],
) -> None:
    with session_factory() as session:
        slot = make_slot(session)
        booking = create_booking(session, slot.id, booking_data())
        booking_id = booking.id

    response = client.get(f"/booking/{booking_id}/success")

    assert response.status_code == 200
    assert "10 октября 2026" in response.text
    assert "10:00" in response.text
    assert "Анна Иванова" in response.text
    assert "+7 999 123-45-67" in response.text
    assert f"/booking/{booking_id}/cancel" in response.text


def test_cancel_confirmation_does_not_mutate(
    client: TestClient,
    session_factory: sessionmaker[Session],
) -> None:
    with session_factory() as session:
        slot = make_slot(session)
        booking = create_booking(session, slot.id, booking_data())
        booking_id = booking.id
        slot_id = slot.id

    response = client.get(f"/booking/{booking_id}/cancel")

    assert response.status_code == 200
    assert "Отменить запись?" in response.text
    assert "10 октября 2026" in response.text
    assert "10:00" in response.text
    assert 'method="post"' in response.text
    assert f'action="/booking/{booking_id}/cancel"' in response.text

    with session_factory() as session:
        assert session.get(Booking, booking_id) is not None
        assert session.get(Slot, slot_id).status == "booked"


def test_post_cancel_booking_redirects_and_frees_slot(
    client: TestClient,
    session_factory: sessionmaker[Session],
) -> None:
    with session_factory() as session:
        slot = make_slot(session)
        booking = create_booking(session, slot.id, booking_data())
        booking_id = booking.id
        slot_id = slot.id

    response = client.post(
        f"/booking/{booking_id}/cancel",
        data={"source": "public"},
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert response.headers["location"] == "/?date=2026-10-10&cancelled=1"

    final_response = client.get(response.headers["location"])
    assert final_response.status_code == 200
    assert "Запись отменена. Время снова доступно для бронирования." in final_response.text

    with session_factory() as session:
        assert session.get(Booking, booking_id) is None
        saved_slot = session.get(Slot, slot_id)
        assert saved_slot is not None
        assert saved_slot.status == "free"


def test_repeat_cancel_booking_is_controlled(
    client: TestClient,
    session_factory: sessionmaker[Session],
) -> None:
    with session_factory() as session:
        slot = make_slot(session)
        booking = create_booking(session, slot.id, booking_data())
        booking_id = booking.id
        slot_id = slot.id

    client.post(f"/booking/{booking_id}/cancel", data={"source": "public"})
    response = client.post(
        f"/booking/{booking_id}/cancel",
        data={"source": "public"},
    )

    assert response.status_code == 404
    assert "уже отменена" in response.text

    with session_factory() as session:
        assert session.get(Booking, booking_id) is None
        assert session.get(Slot, slot_id).status == "free"


def test_nonexistent_cancel_routes_are_controlled(client: TestClient) -> None:
    get_response = client.get("/booking/999999/cancel")
    post_response = client.post(
        "/booking/999999/cancel",
        data={"source": "public"},
    )

    assert get_response.status_code == 404
    assert post_response.status_code == 404
    assert "<html" in get_response.text.lower()
    assert "<html" in post_response.text.lower()


def test_public_schedule_after_cancellation_shows_free_slot(
    client: TestClient,
    session_factory: sessionmaker[Session],
) -> None:
    with session_factory() as session:
        slot = make_slot(session)
        booking = create_booking(session, slot.id, booking_data())
        booking_id = booking.id

    client.post(f"/booking/{booking_id}/cancel", data={"source": "public"})
    response = client.get("/?date=2026-10-10")

    assert response.status_code == 200
    assert "10:00" in response.text
    assert "Свободно" in response.text
    assert "Записаться" in response.text


def test_route_rebooking_after_cancellation(
    client: TestClient,
    session_factory: sessionmaker[Session],
) -> None:
    with session_factory() as session:
        slot = make_slot(session)
        booking = create_booking(session, slot.id, booking_data())
        booking_id = booking.id
        slot_id = slot.id

    client.post(f"/booking/{booking_id}/cancel", data={"source": "public"})

    form_response = client.get(f"/booking/{slot_id}")
    assert form_response.status_code == 200
    assert "Подтвердить запись" in form_response.text

    response = client.post(
        f"/booking/{slot_id}",
        data={
            "client_name": "Иван Петров",
            "client_contact": "ivan@example.com",
        },
        follow_redirects=False,
    )

    assert response.status_code == 303
    with session_factory() as session:
        assert session.scalar(select(func.count()).select_from(Booking)) == 1
        saved_slot = session.get(Slot, slot_id)
        assert saved_slot is not None
        assert saved_slot.status == "booked"


def test_nonexistent_success_page(client: TestClient) -> None:
    response = client.get("/booking/999999/success")

    assert response.status_code == 404
    assert "<html" in response.text.lower()


def test_public_schedule_after_booking(
    client: TestClient,
    session_factory: sessionmaker[Session],
) -> None:
    with session_factory() as session:
        slot = make_slot(session)
        create_booking(session, slot.id, booking_data())

    response = client.get("/?date=2026-10-10")

    assert response.status_code == 200
    assert "10:00" in response.text
    assert "Занято" in response.text
    assert "Записаться" not in response.text
    assert "Анна Иванова" not in response.text
    assert "+7 999 123-45-67" not in response.text


def test_admin_schedule_after_booking(
    client: TestClient,
    session_factory: sessionmaker[Session],
) -> None:
    with session_factory() as session:
        slot = make_slot(session)
        booking = create_booking(session, slot.id, booking_data())
        booking_id = booking.id
        slot_id = slot.id

    response = client.get("/admin?date=2026-10-10")

    assert response.status_code == 200
    assert "10:00" in response.text
    assert "Занято" in response.text
    assert f"/booking/{booking_id}/cancel?source=admin" in response.text
    assert "Анна Иванова" in response.text
    assert "+7 999 123-45-67" in response.text

    confirmation = client.get(f"/booking/{booking_id}/cancel?source=admin")
    assert confirmation.status_code == 200
    assert "Отменить запись?" in confirmation.text
    assert 'name="source" value="admin"' in confirmation.text

    cancelled = client.post(
        f"/booking/{booking_id}/cancel",
        data={"source": "admin"},
        follow_redirects=False,
    )

    assert cancelled.status_code == 303
    assert cancelled.headers["location"] == "/admin?date=2026-10-10&cancelled=1"

    final_response = client.get(cancelled.headers["location"])
    assert final_response.status_code == 200
    assert "Запись отменена. Время снова доступно для бронирования." in final_response.text
    assert "Свободно" in final_response.text

    with session_factory() as session:
        assert session.get(Booking, booking_id) is None
        saved_slot = session.get(Slot, slot_id)
        assert saved_slot is not None
        assert saved_slot.status == "free"
