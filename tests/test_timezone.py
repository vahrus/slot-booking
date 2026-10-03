"""Deterministic clock/configuration tests with a fixed absolute UTC instant."""

import importlib
import time as system_time
from datetime import date, datetime, time, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from app import timezone as app_clock
from app.calendar_view import booking_calendar


FIXED_UTC = datetime(2026, 10, 9, 21, 30, tzinfo=timezone.utc)


@pytest.fixture
def frozen_clock(monkeypatch: pytest.MonkeyPatch) -> None:
    class FrozenDateTime(datetime):
        @classmethod
        def now(cls, tz=None):
            # Also prove callers never ask for a naive/system-local datetime.
            assert tz is not None
            return FIXED_UTC.astimezone(tz)

    monkeypatch.setattr(app_clock, "datetime", FrozenDateTime)


def test_default_timezone_is_moscow(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("APP_TIMEZONE", raising=False)
    assert app_clock.get_app_timezone().key == "Europe/Moscow"


@pytest.mark.parametrize("name, offset, hour", [
    ("Europe/Moscow", 3, 0),
    ("Europe/Istanbul", 3, 0),
    ("Asia/Dubai", 4, 1),
])
def test_override_and_aware_local_datetime(
    monkeypatch: pytest.MonkeyPatch, frozen_clock: None,
    name: str, offset: int, hour: int,
) -> None:
    monkeypatch.setenv("APP_TIMEZONE", name)
    assert app_clock.get_app_timezone().key == name
    now = app_clock.local_now()
    assert now.tzinfo.key == name
    assert now.utcoffset() == timedelta(hours=offset)
    assert now == FIXED_UTC
    assert (now.date(), now.hour, now.minute) == (date(2026, 10, 10), hour, 30)
    assert app_clock.local_today() == date(2026, 10, 10)


@pytest.mark.parametrize("name, expected_today", [
    ("Europe/Moscow", date(2026, 10, 10)),
    ("Asia/Dubai", date(2026, 10, 10)),
    ("Pacific/Honolulu", date(2026, 10, 9)),
])
def test_calendar_today_uses_application_clock(
    monkeypatch: pytest.MonkeyPatch, frozen_clock: None,
    name: str, expected_today: date,
) -> None:
    monkeypatch.setenv("APP_TIMEZONE", name)
    calendar = booking_calendar(None)
    assert calendar["today"] == expected_today
    assert calendar["today_url"] == f"/?date={expected_today.isoformat()}"


@pytest.mark.parametrize("name, past", [
    ("Europe/Moscow", False),
    ("Europe/Istanbul", False),
    ("Asia/Dubai", True),
])
def test_same_wall_time_slot_is_past_or_future_in_configured_timezone(
    monkeypatch: pytest.MonkeyPatch, frozen_clock: None, name: str, past: bool,
) -> None:
    monkeypatch.setenv("APP_TIMEZONE", name)
    # At the fixed instant it is 00:30 in Moscow, but 01:30 in Dubai.
    assert app_clock.is_past_slot(date(2026, 10, 10), time(1)) is past


def test_slot_comparison_at_midnight_and_exact_start(
    monkeypatch: pytest.MonkeyPatch, frozen_clock: None,
) -> None:
    monkeypatch.setenv("APP_TIMEZONE", "Europe/Moscow")
    assert app_clock.is_past_slot(date(2026, 10, 9), time(23))
    assert app_clock.is_past_slot(date(2026, 10, 10), time(0, 29))
    assert not app_clock.is_past_slot(date(2026, 10, 10), time(0, 30))
    assert not app_clock.is_past_slot(date(2026, 10, 10), time(0, 31))


def test_daylight_saving_timezone_uses_iana_offsets(
    monkeypatch: pytest.MonkeyPatch, frozen_clock: None,
) -> None:
    monkeypatch.setenv("APP_TIMEZONE", "Europe/Berlin")
    # This fixed October instant is still summer time in Berlin.
    assert app_clock.local_now().utcoffset() == timedelta(hours=2)
    assert app_clock.local_today() == date(2026, 10, 9)


@pytest.mark.parametrize("name", ["Not/A_Timezone", "", " ", "/etc/localtime", "localtime"])
def test_invalid_configuration_has_no_silent_fallback(
    monkeypatch: pytest.MonkeyPatch, name: str,
) -> None:
    monkeypatch.setenv("APP_TIMEZONE", name)
    with pytest.raises(app_clock.TimezoneConfigurationError) as caught:
        app_clock.get_app_timezone()
    assert "APP_TIMEZONE=" + repr(name) in str(caught.value)
    assert "IANA" in str(caught.value)
    with pytest.raises(app_clock.TimezoneConfigurationError):
        app_clock.local_now()


def test_invalid_timezone_fails_at_startup_before_database_initialization(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    main = importlib.import_module("app.main")
    initialized = []
    monkeypatch.setattr(main, "init_db", lambda: initialized.append(True))
    monkeypatch.setenv("APP_TIMEZONE", "Not/A_Timezone")
    with pytest.raises(app_clock.TimezoneConfigurationError, match="APP_TIMEZONE"):
        with TestClient(main.app):
            pytest.fail("Invalid timezone must stop application startup")
    assert initialized == []


def test_valid_default_starts_normally(monkeypatch: pytest.MonkeyPatch) -> None:
    main = importlib.import_module("app.main")
    initialized = []
    monkeypatch.setattr(main, "init_db", lambda: initialized.append(True))
    monkeypatch.delenv("APP_TIMEZONE", raising=False)
    with TestClient(main.app) as client:
        assert client.get("/health").json() == {"status": "ok", "app": "Slotly"}
    assert initialized == [True]


@pytest.mark.skipif(not hasattr(system_time, "tzset"), reason="POSIX timezone switching")
def test_host_timezone_does_not_change_app_dates_or_slot_comparisons(
    monkeypatch: pytest.MonkeyPatch, frozen_clock: None,
) -> None:
    monkeypatch.setenv("APP_TIMEZONE", "Asia/Dubai")
    observations = []
    try:
        # context restores TZ before the final tzset restores process state.
        with monkeypatch.context() as host:
            for system_zone in ["UTC0", "HST10", "JST-9"]:
                host.setenv("TZ", system_zone)
                system_time.tzset()
                observations.append((
                    datetime.fromtimestamp(FIXED_UTC.timestamp()).hour,
                    app_clock.local_now(), app_clock.local_today(),
                    app_clock.is_past_slot(date(2026, 10, 10), time(1)),
                ))
    finally:
        system_time.tzset()
    assert len({value[0] for value in observations}) == 3
    assert all(value[1].tzinfo.key == "Asia/Dubai" for value in observations)
    assert all(value[1] == FIXED_UTC for value in observations)
    assert all(value[2:] == (date(2026, 10, 10), True) for value in observations)
