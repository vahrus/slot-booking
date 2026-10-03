"""Calendar presentation boundaries and navigation, independent of the database."""

import datetime
from urllib.parse import parse_qs, urlsplit

import pytest

from app.calendar_view import booking_calendar


@pytest.mark.parametrize('year, month, count', [
    (2027, 2, 28), (2028, 2, 29), (2026, 4, 30), (2026, 10, 31),
])
def test_complete_monday_first_month(year: int, month: int, count: int) -> None:
    selected = datetime.date(year, month, 15)
    view = booking_calendar(selected, today=selected)
    days = [day for week in view['weeks'] for day in week]
    assert all(len(week) == 7 for week in view['weeks'])
    assert days[0].weekday() == 0
    assert days[-1].weekday() == 6
    assert [day.day for day in days if day.month == month] == list(range(1, count + 1))
    assert all(right - left == datetime.timedelta(days=1) for left, right in zip(days, days[1:]))
    assert view['today'] == selected
    assert view['today_url'] == f'/?date={selected.isoformat()}'


def test_browsing_preserves_selection_and_crosses_year_boundary() -> None:
    selected = datetime.date(2026, 10, 10)
    view = booking_calendar(selected, '2026-12')
    assert view['label'] == 'Декабрь 2026'
    assert parse_qs(urlsplit(view['previous_url']).query) == {'month': ['2026-11'], 'date': ['2026-10-10']}
    assert parse_qs(urlsplit(view['next_url']).query) == {'month': ['2027-01'], 'date': ['2026-10-10']}
    january = booking_calendar(None, '2027-01')
    assert january['previous_url'] == '/?month=2026-12'


@pytest.mark.parametrize('month', ['bad', '2026-13', '0000-01', '10000-01', '202610', '2026-1', '<script>'])
def test_invalid_month_falls_back_to_selected_date(month: str) -> None:
    view = booking_calendar(datetime.date(2026, 10, 10), month)
    assert view['label'] == 'Октябрь 2026'


def test_no_selection_uses_today_without_selecting_it() -> None:
    today = datetime.date(2028, 2, 29)
    view = booking_calendar(None, today=today)
    assert view['label'] == 'Февраль 2028'
    assert view['previous_url'] == '/?month=2028-01'


@pytest.mark.parametrize('selected, unavailable', [
    (datetime.date.min, 'previous_url'), (datetime.date.max, 'next_url'),
])
def test_supported_date_boundaries_do_not_overflow(selected: datetime.date, unavailable: str) -> None:
    view = booking_calendar(selected)
    assert view[unavailable] is None
    assert all(day is None or datetime.date.min <= day <= datetime.date.max for week in view['weeks'] for day in week)
