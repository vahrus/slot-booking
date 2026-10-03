"""Owner UI behavior with the existing isolated booking test fixtures."""
import datetime

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

from app.database.models import Booking, Slot
from app.schemas.slot import SlotCreate
from app.services.bookings import create_booking
from app.services.slots import create_slot
from tests.test_bookings import booking_data, client, make_slot, session_factory
from tests.test_public_ui import Document, assert_descriptions_exist


def test_admin_identity_navigation_labels_and_no_date(client: TestClient) -> None:
    response = client.get("/admin")
    document = Document(response.text)
    assert response.status_code == 200
    assert document.find("html")["lang"] == "ru"
    assert document.find("a", href="#main-content")
    assert document.find("main", id="main-content")
    assert document.find("a", href="/admin")["aria-current"] == "page"
    assert document.find("a", href="/")
    assert "Панель владельца" in document.text
    assert "Открыть страницу записи" in document.text
    assert "Демо-интерфейс без авторизации." in document.text
    assert "Выберите дату, чтобы посмотреть расписание и записи клиентов." in document.text
    for field_id, field_type in [
        ("slot-date", "date"),
        ("slot-time", "time"),
        ("admin-schedule-date", "date"),
    ]:
        field = document.find("input", id=field_id)
        assert field["type"] == field_type
        assert "required" in field
        assert document.find("label", **{"for": field_id})
    assert document.find("form", action="/admin/slots")["method"] == "post"
    assert document.find("form", action="/admin")["method"] == "get"
    assert "Добавить время" in document.text
    assert_descriptions_exist(document)


def test_selected_empty_day_has_summary_and_creation_hint(client: TestClient) -> None:
    response = client.get("/admin?date=2026-10-10")
    document = Document(response.text)
    assert "Расписание на 10 октября 2026" in document.text
    assert "На эту дату расписание ещё не создано" in document.text
    assert "Добавьте первое доступное время" in document.text
    assert document.find("input", id="slot-date")["value"] == "2026-10-10"
    assert document.find("input", id="admin-schedule-date")["value"] == "2026-10-10"
    assert document.find("a", href="/?date=2026-10-10")
    assert response.context["summary"] == {"total": 0, "free": 0, "booked": 0}
    for text in ["Всего 0", "Свободно 0", "Занято 0"]:
        assert text in document.text


def test_mixed_day_order_counts_client_details_and_public_privacy(
    client: TestClient,
    session_factory: sessionmaker[Session],
) -> None:
    with session_factory() as session:
        afternoon = make_slot(session, time_value=datetime.time(14, 30))
        morning = make_slot(session, time_value=datetime.time(9, 0))
        occupied = make_slot(session, time_value=datetime.time(11, 0))
        booking = create_booking(session, occupied.id, booking_data())
        other = create_slot(session, SlotCreate(
            date=datetime.date(2026, 10, 11), time=datetime.time(15, 0),
        ))
        create_booking(session, other.id, booking_data("Другой день", "other@example.com"))
        booking_id = booking.id
        free_ids = [morning.id, afternoon.id]
    response = client.get("/admin?date=2026-10-10")
    document = Document(response.text)
    assert document.text.index("09:00") < document.text.index("11:00") < document.text.index("14:30")
    assert "15:00" not in document.text and "Другой день" not in document.text
    assert "Анна Иванова" in document.text and "+7 999 123-45-67" in document.text
    assert document.find("a", href=f"/booking/{booking_id}/cancel?source=admin")
    assert response.context["summary"] == {"total": 3, "free": 2, "booked": 1}
    for text in ["Всего 3", "Свободно 2", "Занято 1"]:
        assert text in document.text
    assert "booking_id" not in document.text and "slot_id" not in document.text
    for slot_id in free_ids:
        assert not any(
            tag == "a" and attrs.get("href") == f"/booking/{slot_id}"
            for tag, attrs in document.elements
        )
    public = client.get("/?date=2026-10-10")
    assert "Анна Иванова" not in public.text and "+7 999 123-45-67" not in public.text


def test_free_time_has_no_client_fields_or_cancellation(
    client: TestClient,
    session_factory: sessionmaker[Session],
) -> None:
    with session_factory() as session:
        make_slot(session)
    document = Document(client.get("/admin?date=2026-10-10").text)
    assert "10:00" in document.text
    assert "Свободно" in document.text
    assert "Контакт" not in document.text
    assert "Отменить запись" not in document.text
    assert not any(
        tag == "a" and "/cancel" in (attrs.get("href") or "")
        for tag, attrs in document.elements
    )


def test_booking_details_are_escaped_in_admin(
    client: TestClient,
    session_factory: sessionmaker[Session],
) -> None:
    with session_factory() as session:
        create_booking(session, make_slot(session).id, booking_data(
            '<script>alert("name")</script>', '<img src=x onerror="alert(1)">',
        ))
    response = client.get("/admin?date=2026-10-10")
    document = Document(response.text)
    assert '&lt;script&gt;' in response.text and '&lt;img' in response.text
    assert not any(tag in {"script", "img"} for tag, _ in document.elements)
    assert '<script>alert("name")</script>' in document.text


def test_creation_prg_and_refresh_do_not_duplicate_time(
    client: TestClient,
    session_factory: sessionmaker[Session],
) -> None:
    response = client.post("/admin/slots", data={"date": "2026-10-10", "time": "10:00"}, follow_redirects=False)
    assert response.status_code == 303
    result = client.get(response.headers["location"])
    document = Document(result.text)
    assert "Слот 10:00 на 10.10.2026 создан." in document.text
    assert document.find("div", role="status")
    assert result.context["summary"] == {"total": 1, "free": 1, "booked": 0}
    client.get(response.headers["location"])
    with session_factory() as session:
        assert session.scalar(select(func.count()).select_from(Slot)) == 1


def test_duplicate_of_booked_time_preserves_booking_and_shows_friendly_error(
    client: TestClient,
    session_factory: sessionmaker[Session],
) -> None:
    with session_factory() as session:
        slot = make_slot(session)
        booking = create_booking(session, slot.id, booking_data())
        slot_id, booking_id = slot.id, booking.id
    response = client.post("/admin/slots", data={"date": "2026-10-10", "time": "10:00"}, follow_redirects=False)
    assert response.status_code == 303
    assert response.headers["location"] == "/admin?date=2026-10-10&error=duplicate"
    result = client.get(response.headers["location"])
    assert "Слот на выбранные дату и время уже существует." in result.text
    assert Document(result.text).find("div", role="alert")
    assert "IntegrityError" not in result.text and "UNIQUE constraint" not in result.text
    with session_factory() as session:
        assert session.scalar(select(func.count()).select_from(Slot)) == 1
        assert session.get(Booking, booking_id) is not None
        assert session.get(Slot, slot_id).status == "booked"


@pytest.mark.parametrize("values, invalid_id, valid_id, valid_value", [
    ({"date": "", "time": "10:00"}, "slot-date", "slot-time", "10:00"),
    ({"date": "not-a-date", "time": "10:00"}, "slot-date", "slot-time", "10:00"),
    ({"date": "2026-10-10", "time": ""}, "slot-time", "slot-date", "2026-10-10"),
    ({"date": "2026-10-10", "time": "25:00"}, "slot-time", "slot-date", "2026-10-10"),
])
def test_invalid_creation_describes_bad_field_and_preserves_valid_input(
    client: TestClient,
    session_factory: sessionmaker[Session],
    values: dict[str, str], invalid_id: str, valid_id: str, valid_value: str,
) -> None:
    response = client.post("/admin/slots", data=values)
    document = Document(response.text)
    assert response.status_code == 400
    assert "Укажите корректные дату и время." in document.text
    assert document.find("input", id=invalid_id)["aria-invalid"] == "true"
    assert document.find("input", id=valid_id)["value"] == valid_value
    assert_descriptions_exist(document)
    assert "ValidationError" not in document.text and "Traceback" not in document.text
    with session_factory() as session:
        assert session.scalar(select(func.count()).select_from(Slot)) == 0


def test_invalid_filter_date_is_accessible_and_does_not_execute_html(client: TestClient) -> None:
    response = client.get('/admin?date=<script>alert(1)</script>')
    document = Document(response.text)
    assert response.status_code == 200
    assert "Укажите корректную дату расписания." in document.text
    assert document.find("input", id="admin-schedule-date")["aria-invalid"] == "true"
    assert not any(tag == "script" for tag, _ in document.elements)
    assert_descriptions_exist(document)


def test_admin_confirmation_cancel_return_and_new_booking_details(
    client: TestClient,
    session_factory: sessionmaker[Session],
) -> None:
    with session_factory() as session:
        slot_id = make_slot(session).id
        booking_id = create_booking(session, slot_id, booking_data()).id
    url = f"/booking/{booking_id}/cancel?source=admin"
    confirmation = client.get(url)
    document = Document(confirmation.text)
    assert "Панель владельца" in document.text
    assert "10 октября 2026" in document.text and "10:00" in document.text
    assert document.find("a", href="/admin?date=2026-10-10")
    assert document.find("form", action=f"/booking/{booking_id}/cancel")["method"] == "post"
    assert document.find("input", name="source")["value"] == "admin"
    with session_factory() as session:
        assert session.get(Booking, booking_id) is not None
        assert session.get(Slot, slot_id).status == "booked"
    result = client.post(f"/booking/{booking_id}/cancel", data={"source": "admin"}, follow_redirects=False)
    assert result.status_code == 303
    assert result.headers["location"] == "/admin?date=2026-10-10&cancelled=1"
    admin = client.get(result.headers["location"])
    assert "Запись отменена. Проверьте доступное время в расписании." in admin.text
    assert "Анна Иванова" not in admin.text and "+7 999 123-45-67" not in admin.text
    assert admin.context["summary"] == {"total": 1, "free": 1, "booked": 0}
    with session_factory() as session:
        assert session.get(Booking, booking_id) is None
        assert session.get(Slot, slot_id).status == "free"
    booked = client.post(f"/booking/{slot_id}", data={"client_name": "Новый клиент", "client_contact": "new@example.com"}, follow_redirects=False)
    assert booked.status_code == 303
    admin = client.get("/admin?date=2026-10-10")
    assert "Новый клиент" in admin.text and "new@example.com" in admin.text
    assert admin.context["summary"] == {"total": 1, "free": 0, "booked": 1}
    public = client.get("/?date=2026-10-10")
    assert "Новый клиент" not in public.text and "new@example.com" not in public.text


@pytest.mark.parametrize("source", ["https://example.com", "//example.com", "admin?next=https://example.com"])
def test_unknown_cancel_source_falls_back_to_internal_public_schedule(
    client: TestClient,
    session_factory: sessionmaker[Session],
    source: str,
) -> None:
    with session_factory() as session:
        booking_id = create_booking(session, make_slot(session).id, booking_data()).id
    confirmation = client.get(f"/booking/{booking_id}/cancel", params={"source": source})
    assert Document(confirmation.text).find("input", name="source")["value"] == "public"
    response = client.post(f"/booking/{booking_id}/cancel", data={"source": source}, follow_redirects=False)
    assert response.status_code == 303
    assert response.headers["location"] == "/?date=2026-10-10&cancelled=1"
