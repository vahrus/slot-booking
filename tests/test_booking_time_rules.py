"""Application-local today and booking cutoffs, independent of the real clock."""

from datetime import date, datetime, time, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

from app import timezone as app_clock
from app.database.models import Booking, Slot
from app.schemas.slot import SlotCreate
from app.services.bookings import (
    SlotInPastError, create_booking, slot_booking_state,
)
from app.services.slots import create_slot
from tests.test_bookings import booking_data, client, session_factory
from tests.test_public_ui import Document


TODAY = date(2026, 10, 10)
NOW = datetime(2026, 10, 10, 7, 0, tzinfo=timezone.utc)  # Moscow 10:00


@pytest.fixture
def clock(monkeypatch: pytest.MonkeyPatch) -> dict[str, datetime]:
    state = {"instant": NOW}

    class FixedDateTime(datetime):
        @classmethod
        def now(cls, tz=None):
            assert tz is not None
            return state["instant"].astimezone(tz)

    monkeypatch.setattr(app_clock, "datetime", FixedDateTime)
    return state


def add_slot(session: Session, day: date, hour: int, minute: int = 0) -> Slot:
    return create_slot(session, SlotCreate(date=day, time=time(hour, minute)))


@pytest.mark.parametrize("query", ["", "?date="])
def test_home_selects_local_today_and_immediately_shows_slots(
    client: TestClient, session_factory: sessionmaker[Session], clock: dict,
    query: str,
) -> None:
    with session_factory() as session:
        available_id = add_slot(session, TODAY, 11).id
        other_id = add_slot(session, date(2026, 10, 11), 11).id
    response = client.get("/" + query)
    document = Document(response.text)
    assert response.status_code == 200
    assert document.find("input", id="schedule-date")["value"] == "2026-10-10"
    selected = document.find("a", **{"aria-current": "date"})
    assert selected["href"] == "/?date=2026-10-10"
    assert "calendar-day--today" in selected["class"]
    assert "calendar-day--selected" in selected["class"]
    assert "10 октября 2026" in document.text
    assert document.find("a", href=f"/booking/{available_id}")
    assert not any(tag == "a" and attrs.get("href") == f"/booking/{other_id}" for tag, attrs in document.elements)


@pytest.mark.parametrize("name, selected", [
    ("Europe/Moscow", "2026-10-10"),
    ("Asia/Dubai", "2026-10-10"),
    ("Pacific/Honolulu", "2026-10-09"),
])
def test_home_default_date_respects_app_timezone(
    client: TestClient, clock: dict, monkeypatch: pytest.MonkeyPatch,
    name: str, selected: str,
) -> None:
    monkeypatch.setenv("APP_TIMEZONE", name)
    assert Document(client.get("/").text).find("input", id="schedule-date")["value"] == selected


def test_explicit_date_month_browsing_and_manual_input_are_preserved(
    client: TestClient, session_factory: sessionmaker[Session], clock: dict,
) -> None:
    with session_factory() as session:
        tomorrow = add_slot(session, date(2026, 10, 11), 12).id
    document = Document(client.get("/?date=2026-10-11&month=2026-11").text)
    assert "Ноябрь 2026" in document.text
    assert "11 октября 2026" in document.text
    assert document.find("input", id="schedule-date")["value"] == "2026-10-11"
    assert document.find("form", action="/")["method"] == "get"
    assert document.find("a", href=f"/booking/{tomorrow}")
    assert document.find("a", **{"aria-label": "Следующий месяц"})["href"] == "/?month=2026-12&date=2026-10-11"
    default_browsing = Document(client.get("/?month=2026-11").text)
    assert "Ноябрь 2026" in default_browsing.text
    assert default_browsing.find("input", id="schedule-date")["value"] == "2026-10-10"


@pytest.mark.parametrize("day, hour, minute", [
    (date(2026, 10, 9), 23, 59),
    (TODAY, 9, 59),
    (TODAY, 10, 0),
])
def test_service_rejects_past_and_exact_start_without_database_changes(
    session_factory: sessionmaker[Session], clock: dict,
    day: date, hour: int, minute: int,
) -> None:
    with session_factory() as session:
        slot = add_slot(session, day, hour, minute)
        assert slot_booking_state(slot) == "past"
        with pytest.raises(SlotInPastError):
            create_booking(session, slot.id, booking_data())
        assert session.scalar(select(func.count(Booking.id))) == 0
        session.refresh(slot)
        assert slot.status == "free"


@pytest.mark.parametrize("day, hour, minute", [
    (TODAY, 10, 1), (date(2026, 10, 11), 0, 0),
])
def test_service_allows_future_today_and_future_dates(
    session_factory: sessionmaker[Session], clock: dict,
    day: date, hour: int, minute: int,
) -> None:
    with session_factory() as session:
        slot = add_slot(session, day, hour, minute)
        assert slot_booking_state(slot) == "available"
        booking = create_booking(session, slot.id, booking_data())
        assert booking.slot_id == slot.id
        assert slot.status == "booked"


@pytest.mark.parametrize("day, hour", [(date(2026, 10, 9), 12), (TODAY, 9), (TODAY, 10)])
def test_direct_get_and_post_to_past_slot_are_rejected(
    client: TestClient, session_factory: sessionmaker[Session], clock: dict,
    day: date, hour: int,
) -> None:
    with session_factory() as session:
        slot_id = add_slot(session, day, hour).id
    for response in [
        client.get(f"/booking/{slot_id}"),
        client.post(f"/booking/{slot_id}", data={"client_name": "Клиент", "client_contact": "@client"}),
        client.post(f"/booking/{slot_id}", data={"client_name": "", "client_contact": ""}),
    ]:
        document = Document(response.text)
        assert response.status_code == 409
        assert "Время прошло" in document.text
        assert "Это время уже занято" not in document.text
        assert not any(tag == "form" for tag, _ in document.elements)
    with session_factory() as session:
        assert session.scalar(select(func.count(Booking.id))) == 0
        assert session.get(Slot, slot_id).status == "free"


def test_previously_opened_form_cannot_book_after_start(
    client: TestClient, session_factory: sessionmaker[Session], clock: dict,
) -> None:
    with session_factory() as session:
        slot_id = add_slot(session, TODAY, 10).id
    clock["instant"] = NOW.replace(hour=6, minute=59)
    assert client.get(f"/booking/{slot_id}").status_code == 200
    clock["instant"] = NOW
    response = client.post(f"/booking/{slot_id}", data={"client_name": "Клиент", "client_contact": "@client"})
    assert response.status_code == 409
    assert "Время прошло" in response.text


def test_schedule_has_available_booked_and_disabled_past_states(
    client: TestClient, session_factory: sessionmaker[Session], clock: dict,
) -> None:
    with session_factory() as session:
        past_id = add_slot(session, TODAY, 9).id
        exact_id = add_slot(session, TODAY, 10).id
        free_id = add_slot(session, TODAY, 11).id
        booked = add_slot(session, TODAY, 12)
        create_booking(session, booked.id, booking_data())
        booked_id = booked.id
    document = Document(client.get("/").text)
    slots = [attrs for tag, attrs in document.elements if tag == "li" and "public-slot" in (attrs.get("class") or "").split()]
    assert len(slots) == 4
    assert sum("public-slot--past" in attrs["class"] for attrs in slots) == 2
    assert sum("public-slot--booked" in attrs["class"] for attrs in slots) == 1
    assert all(attrs.get("aria-disabled") == "true" for attrs in slots if "--" in attrs["class"])
    hrefs = [attrs.get("href") for tag, attrs in document.elements if tag == "a"]
    assert f"/booking/{free_id}" in hrefs
    assert all(f"/booking/{slot_id}" not in hrefs for slot_id in [past_id, exact_id, booked_id])
    assert all(text in document.text for text in ["Свободно", "Занято", "Время прошло"])
    assert "1 доступно для записи из 4" in document.text


@pytest.mark.parametrize("source", ["public", "admin"])
def test_existing_past_booking_remains_booked_and_can_be_cancelled(
    client: TestClient, session_factory: sessionmaker[Session], clock: dict, source: str,
) -> None:
    clock["instant"] = datetime(2026, 10, 8, 7, tzinfo=timezone.utc)
    with session_factory() as session:
        slot = add_slot(session, date(2026, 10, 9), 9)
        booking_id = create_booking(session, slot.id, booking_data()).id
        slot_id = slot.id
    clock["instant"] = NOW
    document = Document(client.get("/?date=2026-10-09").text)
    assert document.find("li", **{"class": "public-slot public-slot--booked"})
    assert "Занято" in document.text
    assert "Время прошло" not in document.text
    assert client.get(f"/booking/{booking_id}/cancel?source={source}").status_code == 200
    result = client.post(f"/booking/{booking_id}/cancel", data={"source": source}, follow_redirects=False)
    assert result.status_code == 303
    assert result.headers["location"] == ("/admin" if source == "admin" else "/") + "?date=2026-10-09&cancelled=1"
    with session_factory() as session:
        assert session.get(Booking, booking_id) is None
        slot = session.get(Slot, slot_id)
        assert slot.status == "free"
        assert slot_booking_state(slot) == "past"
        with pytest.raises(SlotInPastError):
            create_booking(session, slot_id, booking_data())
    after = Document(client.get("/?date=2026-10-09").text)
    assert "Время прошло" in after.text
    confirmation = Document(client.get(result.headers["location"]).text)
    assert "Запись отменена. Проверьте доступное время в расписании." in confirmation.text
    assert "Время снова доступно для бронирования" not in confirmation.text
    assert not any(tag == "a" and attrs.get("href") == f"/booking/{slot_id}" for tag, attrs in after.elements)


@pytest.mark.parametrize("name, status", [("Europe/Moscow", 303), ("Asia/Dubai", 409)])
def test_actual_booking_enforcement_uses_app_timezone(
    client: TestClient, session_factory: sessionmaker[Session], clock: dict,
    monkeypatch: pytest.MonkeyPatch, name: str, status: int,
) -> None:
    clock["instant"] = datetime(2026, 10, 9, 21, 30, tzinfo=timezone.utc)
    monkeypatch.setenv("APP_TIMEZONE", name)
    with session_factory() as session:
        slot_id = add_slot(session, TODAY, 1).id
    response = client.post(f"/booking/{slot_id}", data={"client_name": "Клиент", "client_contact": "@client"}, follow_redirects=False)
    assert response.status_code == status
    with session_factory() as session:
        assert session.scalar(select(func.count(Booking.id))) == (1 if status == 303 else 0)
