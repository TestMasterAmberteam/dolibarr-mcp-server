"""Time-report collection, filtering, paging, and aggregation tests."""

from __future__ import annotations

from datetime import UTC, date, datetime

import pytest

from dolibarr_mcp.errors import InvalidDolibarrResponseError, ReportRequestError
from dolibarr_mcp.models import (
    DolibarrProjectPayload,
    DolibarrTaskPayload,
    DolibarrTimeEntryPayload,
    DolibarrUserPayload,
    SummaryGroup,
    VerifiedIdentity,
)
from dolibarr_mcp.reporting import MAX_NOTE_LENGTH, TimeReportingService

pytestmark = pytest.mark.anyio


def timestamp(day: int) -> int:
    return int(datetime(2026, 8, day, 12, tzinfo=UTC).timestamp())


def line(
    entry_id: int,
    *,
    day: int,
    duration: int,
    user_id: int,
    task_id: int,
    task_ref: str,
    task_label: str,
    note: str = "work",
    product_id: str = "0",
) -> DolibarrTimeEntryPayload:
    return DolibarrTimeEntryPayload.model_validate(
        {
            "timespent_line_id": str(entry_id),
            "timespent_line_date": timestamp(day),
            "timespent_line_datehour": timestamp(day),
            "timespent_line_duration": str(duration),
            "timespent_line_fk_user": str(user_id),
            "timespent_line_fk_product": product_id,
            "timespent_line_note": note,
            "fk_project": "10",
            "project_ref": "P-10",
            "project_label": "Project Ten",
            "fk_task": str(task_id),
            "task_ref": task_ref,
            "task_label": task_label,
        }
    )


class FakeReportingClient:
    def __init__(self) -> None:
        self.project = DolibarrProjectPayload.model_validate(
            {"id": "10", "ref": "P-10", "title": "Project Ten"}
        )
        self.tasks = [
            DolibarrTaskPayload.model_validate(
                {
                    "id": "100",
                    "fk_project": "10",
                    "ref": "T-100",
                    "label": "Analysis",
                    "lines": [
                        line(
                            1,
                            day=1,
                            duration=3600,
                            user_id=1,
                            task_id=100,
                            task_ref="T-100",
                            task_label="Analysis",
                            note="a" * (MAX_NOTE_LENGTH + 50),
                        ),
                        line(
                            2,
                            day=1,
                            duration=1800,
                            user_id=2,
                            task_id=100,
                            task_ref="T-100",
                            task_label="Analysis",
                        ),
                    ],
                }
            ),
            DolibarrTaskPayload.model_validate(
                {
                    "id": "101",
                    "fk_project": "10",
                    "ref": "T-101",
                    "label": "Delivery",
                    "lines": [
                        line(
                            3,
                            day=8,
                            duration=7200,
                            user_id=1,
                            task_id=101,
                            task_ref="T-101",
                            task_label="Delivery",
                        )
                    ],
                }
            ),
        ]
        self.users = [
            DolibarrUserPayload.model_validate(
                {"id": 1, "login": "alice", "firstname": "Alice", "lastname": "Able"}
            ),
            DolibarrUserPayload.model_validate(
                {"id": 2, "login": "bob", "firstname": "Bob", "lastname": "Baker"}
            ),
        ]
        self.calls: list[str] = []

    async def get_project(self, api_key: str, project_id: int) -> DolibarrProjectPayload:
        assert api_key == "key"
        assert project_id == 10
        self.calls.append("project")
        return self.project.model_copy(deep=True)

    async def get_task(self, api_key: str, task_id: int) -> DolibarrTaskPayload:
        assert api_key == "key"
        self.calls.append("task")
        return next(task.model_copy(deep=True) for task in self.tasks if task.task_id == task_id)

    async def get_task_timespent(
        self,
        api_key: str,
        task_id: int,
    ) -> list[DolibarrTimeEntryPayload]:
        assert api_key == "key"
        self.calls.append(f"times:{task_id}")
        task = next(task for task in self.tasks if task.task_id == task_id)
        return [entry.model_copy(deep=True) for entry in task.lines or []]

    async def get_project_tasks(
        self,
        api_key: str,
        project_id: int,
    ) -> list[DolibarrTaskPayload]:
        assert api_key == "key"
        assert project_id == 10
        self.calls.append("project_tasks")
        return [task.model_copy(deep=True) for task in self.tasks]

    async def list_tasks(self, api_key: str) -> list[DolibarrTaskPayload]:
        assert api_key == "key"
        self.calls.append("tasks")
        tasks = [task.model_copy(deep=True) for task in self.tasks]
        for task in tasks:
            task.lines = None
        return tasks

    async def list_users(self, api_key: str) -> list[DolibarrUserPayload]:
        assert api_key == "key"
        self.calls.append("users")
        return [user.model_copy(deep=True) for user in self.users]


def identity() -> VerifiedIdentity:
    return VerifiedIdentity(
        user_id=1,
        login="alice",
        first_name="Alice",
        last_name="Able",
    )


async def test_my_time_report_groups_by_day_project_and_task() -> None:
    client = FakeReportingClient()
    report = await TimeReportingService(client).my_time_report(
        "key",
        identity(),
        date_from=date(2026, 8, 1),
        date_to=date(2026, 8, 31),
        limit=1,
    )
    assert report.duration_seconds == 10_800
    assert report.group_count == 2
    assert report.returned_group_count == 1
    assert report.truncated is True
    assert report.rows[0].day == date(2026, 8, 1)
    assert report.rows[0].task.ref == "T-100"
    assert sorted(call for call in client.calls if call.startswith("times:")) == [
        "times:100",
        "times:101",
    ]


async def test_project_report_groups_all_authorized_users_and_tasks() -> None:
    report = await TimeReportingService(FakeReportingClient()).project_time_report(
        "key",
        project_id=10,
        date_from=date(2026, 8, 1),
        date_to=date(2026, 8, 31),
        limit=1,
    )
    assert report.duration_seconds == 12_600
    assert report.entry_count == 3
    assert report.user_group_count == 2
    assert report.task_group_count == 2
    assert report.truncated is True
    assert report.by_user[0].user.login == "alice"
    assert report.by_task[0].task.ref == "T-100"


async def test_task_timespent_pages_entries_and_bounds_notes() -> None:
    report = await TimeReportingService(FakeReportingClient()).task_timespent(
        "key",
        task_id=100,
        date_from=date(2026, 8, 1),
        date_to=date(2026, 8, 31),
        offset=0,
        limit=1,
    )
    assert report.duration_seconds == 5400
    assert report.entry_count == 2
    assert report.returned_entry_count == 1
    assert report.has_more is True
    assert report.project.ref == "P-10"
    assert report.entries[0].product_id is None
    assert report.entries[0].note is not None
    assert len(report.entries[0].note) == MAX_NOTE_LENGTH


@pytest.mark.parametrize(
    ("group_by", "expected_keys"),
    [
        ("user", ["1", "2"]),
        ("project", ["10"]),
        ("task", ["100", "101"]),
        ("month", ["2026-08"]),
        ("week", ["2026-W31", "2026-W32"]),
    ],
)
async def test_time_summary_supports_every_group(
    group_by: SummaryGroup,
    expected_keys: list[str],
) -> None:
    report = await TimeReportingService(FakeReportingClient()).time_summary(
        "key",
        date_from=date(2026, 8, 1),
        date_to=date(2026, 8, 31),
        group_by=group_by,
        user_id=None,
        project_id=None,
        task_id=None,
        limit=20,
    )
    assert [row.key for row in report.rows] == expected_keys
    assert report.duration_seconds == 12_600
    assert report.truncated is False


async def test_time_entries_applies_fixed_filters_and_paging() -> None:
    client = FakeReportingClient()
    report = await TimeReportingService(client).time_entries(
        "key",
        date_from=date(2026, 8, 1),
        date_to=date(2026, 8, 31),
        user_id=1,
        project_id=10,
        task_id=None,
        offset=1,
        limit=1,
    )
    assert report.entry_count == 2
    assert report.returned_entry_count == 1
    assert report.has_more is False
    assert report.entries[0].task.task_id == 101
    assert "project_tasks" in client.calls
    assert "tasks" not in client.calls


async def test_task_filter_uses_narrow_task_collection() -> None:
    client = FakeReportingClient()
    report = await TimeReportingService(client).time_entries(
        "key",
        date_from=date(2026, 8, 1),
        date_to=date(2026, 8, 31),
        user_id=None,
        project_id=10,
        task_id=100,
        offset=0,
        limit=20,
    )
    assert report.entry_count == 2
    assert "task" in client.calls
    assert "project_tasks" not in client.calls


async def test_invalid_period_and_page_are_rejected() -> None:
    service = TimeReportingService(FakeReportingClient())
    with pytest.raises(ReportRequestError):
        await service.my_time_report(
            "key",
            identity(),
            date_from=date(2026, 8, 2),
            date_to=date(2026, 8, 1),
            limit=20,
        )
    with pytest.raises(ReportRequestError):
        await service.time_entries(
            "key",
            date_from=date(2026, 8, 1),
            date_to=date(2026, 8, 2),
            user_id=None,
            project_id=None,
            task_id=None,
            offset=-1,
            limit=20,
        )


async def test_mismatched_upstream_task_line_is_rejected() -> None:
    client = FakeReportingClient()
    assert client.tasks[0].lines is not None
    client.tasks[0].lines[0].task_id = 999
    with pytest.raises(InvalidDolibarrResponseError):
        await TimeReportingService(client).project_time_report(
            "key",
            project_id=10,
            date_from=date(2026, 8, 1),
            date_to=date(2026, 8, 31),
            limit=20,
        )


def test_non_positive_product_identifier_is_unassigned() -> None:
    payload = line(
        99,
        day=1,
        duration=1,
        user_id=1,
        task_id=100,
        task_ref="T-100",
        task_label="Analysis",
        product_id="-1",
    )
    assert payload.product_id is None
