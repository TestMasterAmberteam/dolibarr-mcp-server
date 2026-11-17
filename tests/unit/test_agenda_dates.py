"""Actual IANA transitions and upstream sentinel normalization, without production calls."""

from __future__ import annotations

from datetime import UTC, date, datetime

import pytest
from pydantic import ValidationError

from dolibarr_mcp.agenda_dates import agenda_timezone, event_dates, local_instant, search_dates
from dolibarr_mcp.agenda_models import AgendaEventCreateInput, DolibarrAgendaEventPayload


@pytest.mark.parametrize(
    ("start", "end", "seconds", "offset"),
    [
        ("2026-11-17", None, 86400, 3600),
        ("17.11.2026", "18.11.2026", 172800, 3600),
        ("2026-03-29", None, 23 * 3600, 3600),
        ("2026-10-25", None, 25 * 3600, 7200),
        ("2026-03-28", "2026-03-30", 71 * 3600, 3600),
    ],
)
def test_local_all_day(start: str, end: str | None, seconds: int, offset: int) -> None:
    first, last, full = event_dates(start, end, "Europe/Warsaw", None)
    assert full
    assert int(last.timestamp() - first.timestamp()) + 1 == seconds
    assert first.hour == 0
    assert last.hour == 23
    assert last.minute == 59
    assert last.second == 59
    assert first.utcoffset() is not None
    assert first.utcoffset().total_seconds() == offset  # type: ignore[union-attr]


@pytest.mark.parametrize("offset", ["+02:00", "+01:00"])
def test_explicit_fold(offset: str) -> None:
    first, last, full = event_dates(
        f"2026-10-25T02:30:00{offset}", None, "Europe/Warsaw", all_day=False
    )
    assert not full
    assert first == last
    assert first.isoformat().endswith(offset)


@pytest.mark.parametrize(
    ("start", "end", "timezone", "all_day"),
    [
        ("2026-03-29T02:30:00", None, "Europe/Warsaw", None),
        ("2026-10-25T02:30:00", None, "Europe/Warsaw", None),
        ("2026-03-29T02:30:00+01:00", None, "Europe/Warsaw", False),
        ("2026-11-17T12:00:00+02:00", None, "Europe/Warsaw", False),
        ("2026-11-17T12:00:00.123456", None, "UTC", False),
        ("2026-11-17T12:00:00", "2026-11-17T11:00:00", "UTC", False),
        ("2026-11-18", "2026-11-17", "UTC", None),
        ("2026-11-17", "2026-11-17T12:00:00", "UTC", None),
        ("2026-11-17T12:00:00", "2026-11-17", "UTC", None),
        ("2026-11-17", None, "UTC", False),
        ("2026-11-17T12:00:00", None, "UTC", True),
        ("2026-11-17", None, "Unknown/Zone", None),
        ("2026-11-17", None, "/etc/passwd", None),
        ("2026-11-17", None, "../zone", None),
        ("yesterday", None, "UTC", None),
        ("2026-11-99", None, "UTC", None),
        ("1969-11-17", None, "UTC", None),
        ("2100-11-17", None, "UTC", None),
        ("2011-12-30", None, "Pacific/Apia", None),
    ],
)
def test_invalid_or_ambiguous_date(
    start: str,
    end: str | None,
    timezone: str,
    all_day: bool | None,  # noqa: FBT001 - pytest parameter
) -> None:
    with pytest.raises(ValueError, match=r".+"):
        event_dates(start, end, timezone, all_day)


def test_search_boundaries_and_input_validation() -> None:
    assert search_dates(None, None, "UTC") == (None, None)
    first, last = search_dates(date(2026, 3, 29), date(2026, 3, 29), "Europe/Warsaw")
    assert first is not None
    assert last is not None
    assert last - first + 1 == 23 * 3600
    with pytest.raises(ValueError, match=r".+"):
        search_dates(date(2026, 11, 18), date(2026, 11, 17), "UTC")
    with pytest.raises(ValidationError):
        AgendaEventCreateInput(title="   ", event_type="email", project_id=10, start="2026-11-17")
    instant = datetime(2026, 11, 17, 12, tzinfo=UTC)
    assert local_instant(instant, agenda_timezone("UTC")) == instant


@pytest.mark.parametrize("value", [None, "", 0, "0", -1, "-1"])
def test_optional_sentinels_and_safe_projection(value: object) -> None:
    row = DolibarrAgendaEventPayload.model_validate(
        {
            "id": "42",
            "label": "Auto",
            "type_code": "AC_OTH_AUTO",
            "percentage": "-1",
            "datep": value,
            "datef": value,
            "fk_project": value,
            "socid": value,
            "userownerid": value,
            "fk_element": value,
            "email_to": "secret@example.invalid",
            "array_options": {"hidden": "hidden"},
        }
    )
    assert row.date_start == (0 if value in (0, "0") else None)
    assert row.project_id is None
    assert row.thirdparty_id is None
    assert "secret" not in row.model_dump_json()
    assert "hidden" not in row.model_dump_json()
