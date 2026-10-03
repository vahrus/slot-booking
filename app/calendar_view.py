"""Calendar presentation data; no schedule queries or availability rules."""

import calendar
from datetime import date
from urllib.parse import urlencode


_MONTH_NAMES = (
    "Январь", "Февраль", "Март", "Апрель", "Май", "Июнь",
    "Июль", "Август", "Сентябрь", "Октябрь", "Ноябрь", "Декабрь",
)


def booking_calendar(
    selected: date | None,
    requested_month: str | None = None,
    *,
    today: date | None = None,
) -> dict[str, object]:
    """Build Monday-first weeks and GET links, preserving the selected date."""
    today = today or date.today()
    first = (selected or today).replace(day=1)
    if requested_month:
        try:
            # Strict ISO month: malformed presentation input simply falls back.
            candidate = date.fromisoformat(requested_month + "-01")
            if requested_month == f"{candidate.year:04d}-{candidate.month:02d}":
                first = candidate
        except ValueError:
            pass

    def month_url(year: int, month: int) -> str:
        parameters = {"month": f"{year:04d}-{month:02d}"}
        if selected:
            parameters["date"] = selected.isoformat()
        return "/?" + urlencode(parameters)

    previous = (
        (first.year, first.month - 1) if first.month > 1
        else (first.year - 1, 12)
    )
    following = (
        (first.year, first.month + 1) if first.month < 12
        else (first.year + 1, 1)
    )
    count = calendar.monthrange(first.year, first.month)[1]
    cell_count = ((first.weekday() + count + 6) // 7) * 7
    start = first.toordinal() - first.weekday()
    days = [
        date.fromordinal(ordinal)
        if date.min.toordinal() <= ordinal <= date.max.toordinal() else None
        for ordinal in range(start, start + cell_count)
    ]
    return {
        "month": first.month,
        "label": f"{_MONTH_NAMES[first.month - 1]} {first.year}",
        "weeks": [days[index:index + 7] for index in range(0, len(days), 7)],
        "today": today,
        "today_url": "/?" + urlencode({"date": today.isoformat()}),
        "previous_url": month_url(*previous) if previous[0] >= 1 else None,
        "next_url": month_url(*following) if following[0] <= 9999 else None,
    }
