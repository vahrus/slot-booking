"""Public UI contracts; use the existing isolated booking test database."""
import datetime
from html.parser import HTMLParser

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session, sessionmaker

from tests.test_bookings import (
    booking_data,
    client,
    make_slot,
    session_factory,
)
from app.services.bookings import create_booking


class Document(HTMLParser):
    """Read semantic attributes and visible text without a new dependency."""

    def __init__(self, markup: str) -> None:
        super().__init__()
        self.elements: list[tuple[str, dict[str, str | None]]] = []
        self.words: list[str] = []
        self.feed(markup)

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.elements.append((tag, dict(attrs)))

    def handle_data(self, data: str) -> None:
        self.words.append(data)

    def find(self, tag: str, **attributes: str) -> dict[str, str | None]:
        return next(
            attrs for element, attrs in self.elements
            if element == tag and all(attrs.get(key) == value for key, value in attributes.items())
        )

    @property
    def text(self) -> str:
        return " ".join(" ".join(self.words).split())


def assert_descriptions_exist(document: Document) -> None:
    ids = {attrs.get("id") for _, attrs in document.elements}
    for _, attrs in document.elements:
        for reference in (attrs.get("aria-describedby") or "").split():
            assert reference in ids


@pytest.mark.parametrize("query", ["", "?date="])
def test_no_date_explains_next_step(client: TestClient, query: str) -> None:
    response = client.get("/" + query)
    document = Document(response.text)
    assert response.status_code == 200
    assert "Выберите дату, чтобы посмотреть доступное время." in document.text
    assert document.find("input", name="date")["type"] == "date"
    assert document.find("label", **{"for": "schedule-date"})
    assert not any(tag == "a" and (attrs.get("href") or "").startswith("/booking/") for tag, attrs in document.elements)


def test_empty_schedule_preserves_selected_date(client: TestClient) -> None:
    response = client.get("/?date=2026-10-10")
    document = Document(response.text)
    assert "На эту дату пока нет расписания" in document.text
    assert "Выберите другую дату" in document.text
    assert document.find("input", name="date")["value"] == "2026-10-10"
    assert "10 октября 2026" in document.text


def test_invalid_date_error_is_connected_to_input(client: TestClient) -> None:
    response = client.get('/?date=<script>alert(1)</script>')
    document = Document(response.text)
    date_input = document.find("input", name="date")
    assert response.status_code == 200
    assert date_input["aria-invalid"] == "true"
    assert "date-error" in date_input["aria-describedby"]
    assert "Укажите корректную дату." in document.text
    assert not any(tag == "script" for tag, _ in document.elements)
    assert_descriptions_exist(document)


def test_mixed_schedule_links_only_free_time_and_hides_client_data(
    client: TestClient, session_factory: sessionmaker[Session],
) -> None:
    with session_factory() as session:
        free = make_slot(session, time_value=datetime.time(9, 0))
        occupied = make_slot(session, time_value=datetime.time(11, 0))
        create_booking(session, occupied.id, booking_data())
        free_id, occupied_id = free.id, occupied.id
    document = Document(client.get("/?date=2026-10-10").text)
    hrefs = [attrs.get("href") for tag, attrs in document.elements if tag == "a"]
    assert f"/booking/{free_id}" in hrefs
    assert f"/booking/{occupied_id}" not in hrefs
    assert "Свободно" in document.text and "Занято" in document.text
    assert document.text.index("09:00") < document.text.index("11:00")
    assert "Анна Иванова" not in document.text
    assert "+7 999 123-45-67" not in document.text


def test_booking_labels_context_and_return_date(
    client: TestClient, session_factory: sessionmaker[Session],
) -> None:
    with session_factory() as session:
        slot_id = make_slot(session).id
    document = Document(client.get(f"/booking/{slot_id}").text)
    assert "10 октября 2026" in document.text and "10:00" in document.text
    for name, input_id in [("client_name", "client-name"), ("client_contact", "client-contact")]:
        field = document.find("input", name=name)
        assert field["id"] == input_id
        assert document.find("label", **{"for": input_id})
        assert "required" in field
        assert field["type"] == "text"
    assert document.find("input", name="client_name")["autocomplete"] == "name"
    assert document.find("a", href="/?date=2026-10-10")
    assert "Телефон, email или username в мессенджере." in document.text
    assert_descriptions_exist(document)


@pytest.mark.parametrize("invalid_field, invalid_value, expected", [
    ("client_name", "", "Введите имя."),
    ("client_name", "   ", "Введите имя."),
    ("client_name", "А" * 101, "Имя слишком длинное."),
    ("client_contact", "", "Укажите контакт для связи."),
    ("client_contact", "   ", "Укажите контакт для связи."),
    ("client_contact", "a" * 101, "Контакт слишком длинный."),
])
def test_validation_preserves_other_field_and_describes_error(
    client: TestClient, session_factory: sessionmaker[Session],
    invalid_field: str, invalid_value: str, expected: str,
) -> None:
    with session_factory() as session:
        slot_id = make_slot(session).id
    values = {"client_name": "Алексей", "client_contact": "@alex"}
    values[invalid_field] = invalid_value
    response = client.post(f"/booking/{slot_id}", data=values)
    document = Document(response.text)
    assert response.status_code == 400
    assert expected in document.text
    assert "ValidationError" not in document.text and "Value error" not in document.text
    for name, value in values.items():
        assert document.find("input", name=name)["value"] == value
    invalid = document.find("input", name=invalid_field)
    assert invalid["aria-invalid"] == "true"
    assert invalid["aria-describedby"]
    assert_descriptions_exist(document)


def test_user_values_are_escaped_in_error_success_and_cancellation(
    client: TestClient, session_factory: sessionmaker[Session],
) -> None:
    with session_factory() as session:
        slot_id = make_slot(session).id
    values = {"client_name": '<script>alert("name")</script>', "client_contact": '<img src=x onerror="alert(1)">'}
    error = client.post(f"/booking/{slot_id}", data={**values, "client_contact": ""})
    result = client.post(f"/booking/{slot_id}", data=values, follow_redirects=False)
    success = client.get(result.headers["location"])
    cancel_url = next(attrs["href"] for tag, attrs in Document(success.text).elements if tag == "a" and (attrs.get("href") or "").endswith("/cancel"))
    confirmation = client.get(cancel_url)
    for response in [error, success, confirmation]:
        document = Document(response.text)
        assert not any(tag in {"script", "img"} for tag, _ in document.elements)
        assert "&lt;script&gt;" in response.text
    assert Document(error.text).find("input", name="client_name")["value"] == values["client_name"]


def test_success_actions_and_no_visible_technical_ids(
    client: TestClient, session_factory: sessionmaker[Session],
) -> None:
    with session_factory() as session:
        booking = create_booking(session, make_slot(session).id, booking_data())
        booking_id = booking.id
    document = Document(client.get(f"/booking/{booking_id}/success").text)
    for expected in ["Запись подтверждена", "10 октября 2026", "10:00", "Анна Иванова", "+7 999 123-45-67"]:
        assert expected in document.text
    assert document.find("a", href=f"/booking/{booking_id}/cancel")
    assert document.find("a", href="/?date=2026-10-10")
    assert "booking_id" not in document.text and "slot_id" not in document.text


@pytest.mark.parametrize("source, expected_back", [("public", "/?date=2026-10-10"), ("admin", "/admin?date=2026-10-10")])
def test_cancel_keep_action_preserves_source_and_date(
    client: TestClient, session_factory: sessionmaker[Session], source: str, expected_back: str,
) -> None:
    with session_factory() as session:
        booking_id = create_booking(session, make_slot(session).id, booking_data()).id
    document = Document(client.get(f"/booking/{booking_id}/cancel?source={source}").text)
    assert document.find("a", href=expected_back)
    assert "Оставить запись" in document.text and "Отменить запись?" in document.text
    assert document.find("form", action=f"/booking/{booking_id}/cancel")["method"] == "post"
    assert document.find("input", name="source")["value"] == source


@pytest.mark.parametrize("method", ["get", "post"])
def test_conflict_has_navigation_and_no_booking_form(
    client: TestClient, session_factory: sessionmaker[Session], method: str,
) -> None:
    with session_factory() as session:
        slot_id = make_slot(session).id
        create_booking(session, slot_id, booking_data())
    response = getattr(client, method)(f"/booking/{slot_id}", **({"data": {"client_name": "Другой", "client_contact": "@other"}} if method == "post" else {}))
    document = Document(response.text)
    assert response.status_code == 409
    assert "Это время уже занято" in document.text
    assert document.find("a", href="/?date=2026-10-10")
    assert not any(tag == "form" for tag, _ in document.elements)
    assert "Анна Иванова" not in document.text


@pytest.mark.parametrize("path", ["/booking/999999", "/booking/999999/success", "/booking/999999/cancel"])
def test_controlled_errors_share_accessible_public_shell(client: TestClient, path: str) -> None:
    response = client.get(path)
    document = Document(response.text)
    assert response.status_code == 404
    assert document.find("html")["lang"] == "ru"
    assert document.find("a", href="#main-content")
    assert document.find("main", id="main-content")
    assert document.find("nav")["aria-label"] == "Основная навигация"
    assert document.find("a", href="/")
    assert "Traceback" not in document.text
