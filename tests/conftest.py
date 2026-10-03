"""Keep legacy schedule fixtures deterministic as real dates advance."""

from datetime import datetime, timezone

import pytest

from app import timezone as app_clock


@pytest.fixture(autouse=True)
def fixed_application_clock(monkeypatch: pytest.MonkeyPatch) -> None:
    instant = datetime(2026, 10, 9, 0, 0, tzinfo=timezone.utc)

    class FixedDateTime(datetime):
        @classmethod
        def now(cls, tz=None):
            assert tz is not None
            return instant.astimezone(tz)

    monkeypatch.setenv("APP_TIMEZONE", "Europe/Moscow")
    monkeypatch.setattr(app_clock, "datetime", FixedDateTime)
