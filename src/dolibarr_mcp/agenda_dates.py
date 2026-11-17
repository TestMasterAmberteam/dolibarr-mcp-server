"""Explicit local agenda dates, inclusive all-day boundaries, and DST validation."""

from __future__ import annotations

import re
from datetime import UTC, date, datetime, time
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

_DATE = re.compile(r"^(?:\d{4}-\d{2}-\d{2}|\d{2}\.\d{2}\.\d{4})$")
_MIN_YEAR = 1970
_MAX_YEAR = 2099


def agenda_timezone(name: str) -> ZoneInfo:
    """Resolve a bounded IANA name without exposing filesystem errors."""
    try:
        return ZoneInfo(name)
    except (ValueError, ZoneInfoNotFoundError):
        message = "Unknown IANA timezone."
        raise ValueError(message) from None


def local_instant(value: datetime, zone: ZoneInfo) -> datetime:
    """Require one real local instant, or an explicit offset resolving a DST fold."""
    if value.microsecond:
        message = "Agenda dates must have whole-second precision."
        raise ValueError(message)
    if value.tzinfo is not None:
        normalized = value.astimezone(zone)
        if normalized.replace(tzinfo=None) != value.replace(tzinfo=None):
            message = "The explicit UTC offset does not match the selected timezone."
            raise ValueError(message)
        return normalized
    candidates: dict[int, datetime] = {}
    for fold in (0, 1):
        candidate = value.replace(tzinfo=zone, fold=fold)
        if candidate.astimezone(UTC).astimezone(zone).replace(tzinfo=None) == value:
            candidates[int(candidate.timestamp())] = candidate
    if len(candidates) != 1:
        message = (
            "Local time is nonexistent or ambiguous; use a valid time with an explicit offset."
        )
        raise ValueError(message)
    return next(iter(candidates.values()))


def _parse(value: str) -> date | datetime:
    if _DATE.fullmatch(value):
        if "." in value:
            day, month, year = map(int, value.split("."))
            return date(year, month, day)
        return date.fromisoformat(value)
    if "T" not in value and " " not in value:
        message = "Use YYYY-MM-DD, DD.MM.YYYY, or an ISO date-time."
        raise ValueError(message)
    return datetime.fromisoformat(value)


def event_dates(
    start: str,
    end: str | None,
    timezone: str,
    all_day: bool | None,  # noqa: FBT001 - date parser input
) -> tuple[datetime, datetime, bool]:
    """Normalize a date/range like Dolibarr's local all-day form, never UTC arithmetic."""
    zone = agenda_timezone(timezone)
    first = _parse(start)
    last = _parse(end) if end is not None else first
    date_only = not isinstance(first, datetime)
    full_day = date_only if all_day is None else all_day
    if full_day != date_only or isinstance(last, datetime) == date_only:
        message = "All-day events require dates; timed events require date-times at both ends."
        raise ValueError(message)
    if full_day:
        first_dt = local_instant(datetime.combine(first, time.min), zone)
        last_dt = local_instant(datetime.combine(last, time(23, 59, 59)), zone)
    else:
        # The mixed-input check above guarantees these types.
        assert isinstance(first, datetime)  # noqa: S101 - narrowed by date-only validation above
        assert isinstance(last, datetime)  # noqa: S101 - narrowed by mixed-input validation above
        first_dt, last_dt = local_instant(first, zone), local_instant(last, zone)
    if first_dt.timestamp() > last_dt.timestamp():
        message = "Agenda end must not precede start."
        raise ValueError(message)
    if not _MIN_YEAR <= first_dt.year <= last_dt.year <= _MAX_YEAR or first_dt.timestamp() < 0:
        message = "Agenda dates must be within 1970 through 2099."
        raise ValueError(message)
    return first_dt, last_dt, full_day


def search_dates(
    date_from: date | None, date_to: date | None, timezone: str
) -> tuple[int | None, int | None]:
    """Return inclusive local-day search bounds, checking the zone even without dates."""
    zone = agenda_timezone(timezone)
    if date_from is not None and date_to is not None and date_from > date_to:
        message = "Agenda date_from must not follow date_to."
        raise ValueError(message)
    lower = (
        int(local_instant(datetime.combine(date_from, time.min), zone).timestamp())
        if date_from is not None
        else None
    )
    upper = (
        int(local_instant(datetime.combine(date_to, time(23, 59, 59)), zone).timestamp())
        if date_to is not None
        else None
    )
    return lower, upper
