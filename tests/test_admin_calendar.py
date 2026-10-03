"""Shared server-rendered calendar and admin behavior at a fixed app time."""

from datetime import date, datetime, time, timezone
from urllib.parse import parse_qs, urlsplit

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session, sessionmaker

from app.calendar_view import booking_calendar
from app.database.models import Booking, Slot
from app.schemas.slot import SlotCreate
from app.services.bookings import create_booking
from app.services.slots import create_slot
from tests.test_booking_time_rules import clock
from tests.test_bookings import booking_data, client, session_factory
from tests.test_public_ui import Document, assert_descriptions_exist


TODAY = date(2026, 10, 10)


def add_slot(session: Session, day: date, hour: int) -> Slot:
    return create_slot(session, SlotCreate(date=day, time=time(hour)))


def test_admin_defaults_to_today_with_schedule_and_creation_date(
    client: TestClient, session_factory: sessionmaker[Session], clock: dict,
) -> None:
    with session_factory() as session:
        slot_id = add_slot(session, TODAY, 11).id
    response = client.get('/admin')
    document = Document(response.text)
    assert response.status_code == 200
    assert response.context['selected_date'] == TODAY
    assert response.context['slots'][0].id == slot_id
    assert response.context['summary'] == {'total': 1, 'free': 1, 'booked': 0}
    assert document.find('input', id='slot-date')['value'] == TODAY.isoformat()
    assert document.find('input', id='admin-schedule-date')['value'] == TODAY.isoformat()
    selected = document.find('a', **{'aria-current': 'date'})
    assert selected['href'] == '/admin?date=2026-10-10'
    assert 'calendar-day--today' in selected['class']
    assert 'calendar-day--selected' in selected['class']
    assert '11:00' in document.text
    assert not any(tag == 'script' for tag, _ in document.elements)
    assert_descriptions_exist(document)


@pytest.mark.parametrize('zone, expected', [
    ('Europe/Moscow', date(2026, 10, 10)),
    ('Asia/Dubai', date(2026, 10, 10)),
    ('Pacific/Honolulu', date(2026, 10, 9)),
])
def test_admin_today_uses_configured_timezone(
    client: TestClient, clock: dict, monkeypatch: pytest.MonkeyPatch,
    zone: str, expected: date,
) -> None:
    monkeypatch.setenv('APP_TIMEZONE', zone)
    response = client.get('/admin')
    assert response.context['selected_date'] == expected
    assert Document(response.text).find('a', **{'aria-current': 'date'})['href'] == '/admin?date=' + expected.isoformat()


@pytest.mark.parametrize('month', ['2027-02', '2028-02', '2026-04', '2026-12'])
def test_same_helper_grid_and_month_navigation_for_admin_and_public(month: str) -> None:
    public = booking_calendar(TODAY, month, today=TODAY)
    admin = booking_calendar(TODAY, month, today=TODAY, base_url='/admin')
    for key in ['weeks', 'label', 'today', 'month']:
        assert admin[key] == public[key]
    for key in ['previous_url', 'next_url', 'today_url']:
        assert urlsplit(admin[key]).path == '/admin'
        assert urlsplit(public[key]).path == '/'
        assert parse_qs(urlsplit(admin[key]).query) == parse_qs(urlsplit(public[key]).query)


def test_admin_month_navigation_preserves_schedule_selection(
    client: TestClient, session_factory: sessionmaker[Session], clock: dict,
) -> None:
    with session_factory() as session:
        selected_id = add_slot(session, TODAY, 11).id
        other_id = add_slot(session, date(2026, 11, 1), 12).id
    document = Document(client.get('/admin').text)
    next_url = document.find('a', **{'aria-label': 'Следующий месяц'})['href']
    assert next_url == '/admin?month=2026-11&date=2026-10-10'
    response = client.get(next_url)
    assert response.context['selected_date'] == TODAY
    assert [slot.id for slot in response.context['slots']] == [selected_id]
    assert other_id not in [slot.id for slot in response.context['slots']]
    next_document = Document(response.text)
    assert 'Ноябрь 2026' in next_document.text
    assert next_document.find('input', id='slot-date')['value'] == '2026-10-10'
    previous_url = next_document.find('a', **{'aria-label': 'Предыдущий месяц'})['href']
    returned = Document(client.get(previous_url).text)
    assert 'Октябрь 2026' in returned.text
    assert returned.find('a', **{'aria-current': 'date'})['href'] == '/admin?date=2026-10-10'


def test_selecting_day_changes_schedule_and_creation_date(
    client: TestClient, session_factory: sessionmaker[Session], clock: dict,
) -> None:
    with session_factory() as session:
        add_slot(session, TODAY, 11)
        tomorrow = add_slot(session, date(2026, 10, 11), 13).id
    initial = Document(client.get('/admin').text)
    selected_url = initial.find('a', **{'aria-label': '11.10.2026'})['href']
    response = client.get(selected_url)
    document = Document(response.text)
    assert [slot.id for slot in response.context['slots']] == [tomorrow]
    assert document.find('a', **{'aria-current': 'date'})['href'] == selected_url
    assert document.find('input', id='slot-date')['value'] == '2026-10-11'
    assert '13:00' in document.text and '11:00' not in document.text


@pytest.mark.parametrize('day', [date(2026, 10, 8), date(2026, 11, 15)])
def test_create_slot_uses_calendar_date_including_past_dates(
    client: TestClient, session_factory: sessionmaker[Session], clock: dict, day: date,
) -> None:
    document = Document(client.get('/admin?date=' + day.isoformat()).text)
    chosen_date = document.find('input', id='slot-date')['value']
    response = client.post('/admin/slots', data={'date': chosen_date, 'time': '15:00'})
    assert response.status_code == 200
    assert response.context['selected_date'] == day
    assert response.context['summary'] == {'total': 1, 'free': 1, 'booked': 0}
    assert Document(response.text).find('a', **{'aria-current': 'date'})['href'] == '/admin?date=' + day.isoformat()
    slot_id = response.context['slots'][0].id
    with session_factory() as session:
        slot = session.get(Slot, slot_id)
        assert slot.date == day and slot.time == time(15)
    public = Document(client.get('/?date=' + day.isoformat()).text)
    hrefs = [attrs.get('href') for tag, attrs in public.elements if tag == 'a']
    assert (f'/booking/{slot_id}' in hrefs) == (day > TODAY)


def test_admin_can_view_booking_and_cancel_on_a_past_date(
    client: TestClient, session_factory: sessionmaker[Session], clock: dict,
) -> None:
    clock['instant'] = datetime(2026, 10, 7, 7, tzinfo=timezone.utc)
    with session_factory() as session:
        slot = add_slot(session, date(2026, 10, 8), 12)
        booking_id = create_booking(session, slot.id, booking_data()).id
        slot_id = slot.id
    clock['instant'] = datetime(2026, 10, 10, 7, tzinfo=timezone.utc)
    document = Document(client.get('/admin?date=2026-10-08').text)
    assert document.find('a', **{'aria-current': 'date'})['href'] == '/admin?date=2026-10-08'
    assert 'Анна Иванова' in document.text and '+7 999 123-45-67' in document.text
    cancel_url = document.find('a', **{'aria-label': 'Отменить запись на 12:00'})['href']
    assert cancel_url == f'/booking/{booking_id}/cancel?source=admin'
    assert client.get(cancel_url).status_code == 200
    response = client.post(f'/booking/{booking_id}/cancel', data={'source': 'admin'})
    assert response.context['selected_date'] == date(2026, 10, 8)
    assert response.context['summary'] == {'total': 1, 'free': 1, 'booked': 0}
    assert 'Время прошло' in client.get('/?date=2026-10-08').text
    assert client.post(f'/booking/{slot_id}', data={'client_name': 'Клиент', 'client_contact': '@client'}).status_code == 409
    with session_factory() as session:
        assert session.get(Booking, booking_id) is None


def test_public_and_admin_render_the_same_calendar_states(client: TestClient, clock: dict) -> None:
    public = Document(client.get('/').text)
    admin = Document(client.get('/admin').text)
    def days(document):
        return [
            (attrs['class'], attrs.get('aria-current'), attrs.get('aria-label'), urlsplit(attrs['href']).query)
            for tag, attrs in document.elements
            if tag == 'a' and 'calendar-day' in (attrs.get('class') or '').split()
        ]
    assert days(public) == days(admin)
    assert len(days(admin)) == 35
    assert public.find('a', **{'aria-current': 'date'})['href'] == '/?date=2026-10-10'


def test_invalid_create_retains_calendar_selection(client: TestClient, clock: dict) -> None:
    response = client.post('/admin/slots', data={'date': '2026-10-10', 'time': 'invalid'})
    assert response.status_code == 400
    document = Document(response.text)
    assert document.find('a', **{'aria-current': 'date'})['href'] == '/admin?date=2026-10-10'
    assert document.find('input', id='slot-time')['aria-invalid'] == 'true'
