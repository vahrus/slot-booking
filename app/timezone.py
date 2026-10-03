"""The application clock, independent of the host's local timezone."""

import os
from datetime import date, datetime, time, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


DEFAULT_APP_TIMEZONE = "Europe/Moscow"


class TimezoneConfigurationError(ValueError):
    """APP_TIMEZONE does not identify an available IANA timezone."""


def get_app_timezone() -> ZoneInfo:
    """Read and validate the explicit application timezone."""
    name = os.environ.get("APP_TIMEZONE", DEFAULT_APP_TIMEZONE)
    try:
        if name == "localtime":
            raise ValueError("The host-local timezone is not an IANA timezone.")
        return ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError) as error:
        raise TimezoneConfigurationError(
            f"Invalid APP_TIMEZONE={name!r}: expected an available IANA "
            f"timezone, for example {DEFAULT_APP_TIMEZONE!r}."
        ) from error


def local_now() -> datetime:
    """Return an aware datetime using APP_TIMEZONE, never the host timezone."""
    return datetime.now(get_app_timezone())


def local_today() -> date:
    """Return today's date from the single application clock."""
    return local_now().date()


def is_past_slot(slot_date: date, slot_time: time, *, inclusive: bool = False) -> bool:
    """Compare a slot's local wall time against the application clock.

    This is a time predicate, not a rule prohibiting past bookings.
    SQLite slot dates/times retain their existing local, naive representation.
    Booking uses inclusive=True to reject the exact start instant as well.
    """
    now = local_now()
    starts_at = datetime.combine(slot_date, slot_time, tzinfo=now.tzinfo)
    starts_utc = starts_at.astimezone(timezone.utc)
    now_utc = now.astimezone(timezone.utc)
    return starts_utc <= now_utc if inclusive else starts_utc < now_utc
