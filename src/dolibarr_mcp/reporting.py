"""Read-only time-entry collection, normalization, filtering, and aggregation."""

from __future__ import annotations

import asyncio
from collections import defaultdict
from dataclasses import dataclass
from datetime import UTC, date, datetime
from typing import TYPE_CHECKING, Protocol

from dolibarr_mcp.errors import (
    DolibarrResultLimitError,
    InvalidDolibarrResponseError,
    ReportRequestError,
)
from dolibarr_mcp.models import (
    DolibarrProjectPayload,
    DolibarrTaskPayload,
    DolibarrTimeEntryPayload,
    DolibarrUserPayload,
    MyTimeReport,
    MyTimeReportRow,
    ProjectDescriptor,
    ProjectTimeReport,
    SummaryGroup,
    TaskDescriptor,
    TaskTimespentReport,
    TaskTimeTotal,
    TimeEntriesReport,
    TimeEntry,
    TimeSummary,
    TimeSummaryRow,
    UserDescriptor,
    UserTimeTotal,
    VerifiedIdentity,
)

if TYPE_CHECKING:
    from collections.abc import Iterable

MAX_OUTPUT_ROWS = 500
MAX_REPORT_RECORDS = 50_000
MAX_NOTE_LENGTH = 4000
_FANOUT_CONCURRENCY = 10


class ReportingClient(Protocol):
    """Minimal async Dolibarr reads required by the reporting layer."""

    async def get_project(self, api_key: str, project_id: int) -> DolibarrProjectPayload: ...

    async def get_task(self, api_key: str, task_id: int) -> DolibarrTaskPayload: ...

    async def get_task_timespent(
        self,
        api_key: str,
        task_id: int,
    ) -> list[DolibarrTimeEntryPayload]: ...

    async def get_project_tasks(
        self,
        api_key: str,
        project_id: int,
    ) -> list[DolibarrTaskPayload]: ...

    async def list_tasks(self, api_key: str) -> list[DolibarrTaskPayload]: ...

    async def list_users(self, api_key: str) -> list[DolibarrUserPayload]: ...


@dataclass(frozen=True, slots=True)
class _TimeRecord:
    entry_id: int
    day: date
    duration_seconds: int
    user_id: int
    project: ProjectDescriptor
    task: TaskDescriptor
    product_id: int | None
    note: str | None


@dataclass(slots=True)
class _Collection:
    records: list[_TimeRecord]
    users: dict[int, UserDescriptor]
    projects: dict[int, ProjectDescriptor]
    tasks: dict[int, TaskDescriptor]


def _hours(seconds: int) -> float:
    return round(seconds / 3600, 2)


def _duration(seconds: int) -> dict[str, int | float]:
    return {"duration_seconds": seconds, "duration_hours": _hours(seconds)}


def _validate_period(date_from: date, date_to: date) -> None:
    if date_to < date_from:
        raise ReportRequestError


def _validate_page(offset: int, limit: int) -> None:
    if offset < 0 or limit < 1 or limit > MAX_OUTPUT_ROWS:
        raise ReportRequestError


def _validate_group_limit(limit: int) -> None:
    if limit < 1 or limit > MAX_OUTPUT_ROWS:
        raise ReportRequestError


def _user_descriptor(payload: DolibarrUserPayload) -> UserDescriptor:
    return UserDescriptor(
        user_id=payload.id,
        login=payload.login,
        first_name=payload.first_name,
        last_name=payload.last_name,
    )


def _identity_descriptor(identity: VerifiedIdentity) -> UserDescriptor:
    return UserDescriptor(
        user_id=identity.user_id,
        login=identity.login,
        first_name=identity.first_name,
        last_name=identity.last_name,
    )


def _project_descriptor(payload: DolibarrProjectPayload) -> ProjectDescriptor:
    return ProjectDescriptor(
        project_id=payload.project_id,
        ref=payload.ref,
        label=payload.label,
    )


def _task_descriptor(payload: DolibarrTaskPayload) -> TaskDescriptor:
    return TaskDescriptor(task_id=payload.task_id, ref=payload.ref, label=payload.label)


def _safe_day(timestamp: int) -> date:
    try:
        return datetime.fromtimestamp(timestamp, tz=UTC).date()
    except (OverflowError, OSError, ValueError):
        raise InvalidDolibarrResponseError from None


def _normalize_line(
    line: DolibarrTimeEntryPayload,
    *,
    task: DolibarrTaskPayload,
    project: ProjectDescriptor | None,
) -> _TimeRecord:
    if line.task_id != task.task_id:
        raise InvalidDolibarrResponseError
    project_id = line.project_id or task.project_id
    if project_id != task.project_id or (project is not None and project.project_id != project_id):
        raise InvalidDolibarrResponseError
    effective_project = ProjectDescriptor(
        project_id=project_id,
        ref=line.project_ref or (project.ref if project is not None else None),
        label=line.project_label or (project.label if project is not None else None),
    )
    effective_task = TaskDescriptor(
        task_id=task.task_id,
        ref=line.task_ref or task.ref,
        label=line.task_label or task.label,
    )
    note = line.note[:MAX_NOTE_LENGTH] if line.note else None
    timestamp = line.timestamp_with_hour or line.timestamp
    return _TimeRecord(
        entry_id=line.entry_id,
        day=_safe_day(timestamp),
        duration_seconds=line.duration_seconds,
        user_id=line.user_id,
        project=effective_project,
        task=effective_task,
        product_id=line.product_id,
        note=note,
    )


def _normalize_tasks(
    tasks: Iterable[DolibarrTaskPayload],
    *,
    project: ProjectDescriptor | None = None,
) -> list[_TimeRecord]:
    records: list[_TimeRecord] = []
    seen_entry_ids: set[int] = set()
    for task in tasks:
        for line in task.lines or []:
            if line.entry_id in seen_entry_ids:
                raise InvalidDolibarrResponseError
            seen_entry_ids.add(line.entry_id)
            records.append(_normalize_line(line, task=task, project=project))
            if len(records) > MAX_REPORT_RECORDS:
                raise DolibarrResultLimitError
    return records


def _user_label(user: UserDescriptor) -> str:
    name = " ".join(part for part in (user.first_name, user.last_name) if part)
    if user.login and name:
        return f"{user.login} - {name}"[:255]
    return (user.login or name or f"User {user.user_id}")[:255]


def _project_label(project: ProjectDescriptor) -> str:
    parts = [part for part in (project.ref, project.label) if part]
    return " - ".join(parts)[:255] or f"Project {project.project_id}"


def _task_label(task: TaskDescriptor) -> str:
    parts = [part for part in (task.ref, task.label) if part]
    return " - ".join(parts)[:255] or f"Task {task.task_id}"


def _entry(record: _TimeRecord, users: dict[int, UserDescriptor]) -> TimeEntry:
    user = users.get(record.user_id, UserDescriptor(user_id=record.user_id))
    return TimeEntry(
        entry_id=record.entry_id,
        day=record.day,
        **_duration(record.duration_seconds),
        user=user,
        project=record.project,
        task=record.task,
        product_id=record.product_id,
        note=record.note,
    )


def _record_sort_key(record: _TimeRecord) -> tuple[date, str, str, int]:
    return (
        record.day,
        record.project.ref or "",
        record.task.ref or "",
        record.entry_id,
    )


def _filter_records(
    records: Iterable[_TimeRecord],
    *,
    date_from: date,
    date_to: date,
    user_id: int | None = None,
    project_id: int | None = None,
    task_id: int | None = None,
) -> list[_TimeRecord]:
    return sorted(
        (
            record
            for record in records
            if date_from <= record.day <= date_to
            and (user_id is None or record.user_id == user_id)
            and (project_id is None or record.project.project_id == project_id)
            and (task_id is None or record.task.task_id == task_id)
        ),
        key=_record_sort_key,
    )


class TimeReportingService:
    """Stateless reporting operations over one shared Dolibarr client."""

    def __init__(self, client: ReportingClient) -> None:
        self._client = client

    async def my_time_report(
        self,
        api_key: str,
        identity: VerifiedIdentity,
        *,
        date_from: date,
        date_to: date,
        limit: int,
    ) -> MyTimeReport:
        _validate_period(date_from, date_to)
        _validate_group_limit(limit)
        collection = await self._collect(api_key)
        user = _identity_descriptor(identity)
        collection.users[identity.user_id] = user
        records = _filter_records(
            collection.records,
            date_from=date_from,
            date_to=date_to,
            user_id=identity.user_id,
        )
        grouped: dict[tuple[date, int, int], int] = defaultdict(int)
        for record in records:
            grouped[(record.day, record.project.project_id, record.task.task_id)] += (
                record.duration_seconds
            )
        keys = sorted(
            grouped,
            key=lambda key: (
                key[0],
                _project_label(collection.projects[key[1]]),
                _task_label(collection.tasks[key[2]]),
            ),
        )
        rows = [
            MyTimeReportRow(
                day=day,
                project=collection.projects[project_id],
                task=collection.tasks[task_id],
                **_duration(grouped[(day, project_id, task_id)]),
            )
            for day, project_id, task_id in keys[:limit]
        ]
        total_seconds = sum(record.duration_seconds for record in records)
        return MyTimeReport(
            user=user,
            date_from=date_from,
            date_to=date_to,
            group_count=len(keys),
            returned_group_count=len(rows),
            truncated=len(keys) > limit,
            rows=rows,
            **_duration(total_seconds),
        )

    async def project_time_report(
        self,
        api_key: str,
        *,
        project_id: int,
        date_from: date,
        date_to: date,
        limit: int,
    ) -> ProjectTimeReport:
        _validate_period(date_from, date_to)
        _validate_group_limit(limit)
        collection = await self._collect(api_key, project_id=project_id)
        records = _filter_records(
            collection.records,
            date_from=date_from,
            date_to=date_to,
            project_id=project_id,
        )
        by_user_seconds: dict[int, int] = defaultdict(int)
        by_task_seconds: dict[int, int] = defaultdict(int)
        for record in records:
            by_user_seconds[record.user_id] += record.duration_seconds
            by_task_seconds[record.task.task_id] += record.duration_seconds
        user_ids = sorted(
            by_user_seconds,
            key=lambda value: _user_label(
                collection.users.get(value, UserDescriptor(user_id=value))
            ),
        )
        task_ids = sorted(by_task_seconds, key=lambda value: _task_label(collection.tasks[value]))
        by_user = [
            UserTimeTotal(
                user=collection.users.get(user_id, UserDescriptor(user_id=user_id)),
                **_duration(by_user_seconds[user_id]),
            )
            for user_id in user_ids[:limit]
        ]
        by_task = [
            TaskTimeTotal(
                task=collection.tasks[task_id],
                **_duration(by_task_seconds[task_id]),
            )
            for task_id in task_ids[:limit]
        ]
        total_seconds = sum(record.duration_seconds for record in records)
        return ProjectTimeReport(
            project=collection.projects[project_id],
            date_from=date_from,
            date_to=date_to,
            entry_count=len(records),
            user_group_count=len(user_ids),
            task_group_count=len(task_ids),
            truncated=len(user_ids) > limit or len(task_ids) > limit,
            by_user=by_user,
            by_task=by_task,
            **_duration(total_seconds),
        )

    async def task_timespent(
        self,
        api_key: str,
        *,
        task_id: int,
        date_from: date,
        date_to: date,
        offset: int,
        limit: int,
    ) -> TaskTimespentReport:
        _validate_period(date_from, date_to)
        _validate_page(offset, limit)
        collection = await self._collect(api_key, task_id=task_id)
        records = _filter_records(
            collection.records,
            date_from=date_from,
            date_to=date_to,
            task_id=task_id,
        )
        selected = records[offset : offset + limit]
        total_seconds = sum(record.duration_seconds for record in records)
        task = collection.tasks[task_id]
        project = collection.projects[next(iter(collection.projects))]
        return TaskTimespentReport(
            task=task,
            project=project,
            date_from=date_from,
            date_to=date_to,
            entry_count=len(records),
            returned_entry_count=len(selected),
            offset=offset,
            limit=limit,
            has_more=offset + len(selected) < len(records),
            entries=[_entry(record, collection.users) for record in selected],
            **_duration(total_seconds),
        )

    async def time_summary(
        self,
        api_key: str,
        *,
        date_from: date,
        date_to: date,
        group_by: SummaryGroup,
        user_id: int | None,
        project_id: int | None,
        task_id: int | None,
        limit: int,
    ) -> TimeSummary:
        _validate_period(date_from, date_to)
        _validate_group_limit(limit)
        collection = await self._collect_for_filters(
            api_key,
            project_id=project_id,
            task_id=task_id,
        )
        records = _filter_records(
            collection.records,
            date_from=date_from,
            date_to=date_to,
            user_id=user_id,
            project_id=project_id,
            task_id=task_id,
        )
        grouped: dict[str, int] = defaultdict(int)
        labels: dict[str, str] = {}
        for record in records:
            key, label = self._summary_bucket(record, group_by, collection.users)
            grouped[key] += record.duration_seconds
            labels[key] = label
        keys = sorted(grouped)
        rows = [
            TimeSummaryRow(key=key, label=labels[key], **_duration(grouped[key]))
            for key in keys[:limit]
        ]
        total_seconds = sum(record.duration_seconds for record in records)
        return TimeSummary(
            group_by=group_by,
            date_from=date_from,
            date_to=date_to,
            entry_count=len(records),
            group_count=len(keys),
            returned_group_count=len(rows),
            truncated=len(keys) > limit,
            rows=rows,
            **_duration(total_seconds),
        )

    async def time_entries(
        self,
        api_key: str,
        *,
        date_from: date,
        date_to: date,
        user_id: int | None,
        project_id: int | None,
        task_id: int | None,
        offset: int,
        limit: int,
    ) -> TimeEntriesReport:
        _validate_period(date_from, date_to)
        _validate_page(offset, limit)
        collection = await self._collect_for_filters(
            api_key,
            project_id=project_id,
            task_id=task_id,
        )
        records = _filter_records(
            collection.records,
            date_from=date_from,
            date_to=date_to,
            user_id=user_id,
            project_id=project_id,
            task_id=task_id,
        )
        selected = records[offset : offset + limit]
        total_seconds = sum(record.duration_seconds for record in records)
        return TimeEntriesReport(
            date_from=date_from,
            date_to=date_to,
            entry_count=len(records),
            returned_entry_count=len(selected),
            offset=offset,
            limit=limit,
            has_more=offset + len(selected) < len(records),
            entries=[_entry(record, collection.users) for record in selected],
            **_duration(total_seconds),
        )

    async def _collect_for_filters(
        self,
        api_key: str,
        *,
        project_id: int | None,
        task_id: int | None,
    ) -> _Collection:
        if task_id is not None:
            return await self._collect(api_key, task_id=task_id)
        if project_id is not None:
            return await self._collect(api_key, project_id=project_id)
        return await self._collect(api_key)

    async def _collect(
        self,
        api_key: str,
        *,
        project_id: int | None = None,
        task_id: int | None = None,
    ) -> _Collection:
        if project_id is not None and task_id is not None:
            raise ReportRequestError
        if task_id is not None:
            return await self._collect_task(api_key, task_id)
        if project_id is not None:
            return await self._collect_project(api_key, project_id)
        return await self._collect_global(api_key)

    async def _collect_task(self, api_key: str, task_id: int) -> _Collection:
        task, lines, users = await asyncio.gather(
            self._client.get_task(api_key, task_id),
            self._client.get_task_timespent(api_key, task_id),
            self._client.list_users(api_key),
        )
        project_payload = await self._client.get_project(api_key, task.project_id)
        project = _project_descriptor(project_payload)
        task.lines = lines
        records = _normalize_tasks([task], project=project)
        return self._build_collection(records, users, [task], [project])

    async def _collect_project(self, api_key: str, project_id: int) -> _Collection:
        project_payload, tasks, users = await asyncio.gather(
            self._client.get_project(api_key, project_id),
            self._client.get_project_tasks(api_key, project_id),
            self._client.list_users(api_key),
        )
        project = _project_descriptor(project_payload)
        records = _normalize_tasks(tasks, project=project)
        return self._build_collection(records, users, tasks, [project])

    async def _collect_global(self, api_key: str) -> _Collection:
        tasks, users = await asyncio.gather(
            self._client.list_tasks(api_key),
            self._client.list_users(api_key),
        )
        semaphore = asyncio.Semaphore(_FANOUT_CONCURRENCY)

        async def load_lines(task: DolibarrTaskPayload) -> None:
            async with semaphore:
                task.lines = await self._client.get_task_timespent(api_key, task.task_id)

        await asyncio.gather(*(load_lines(task) for task in tasks))
        records = _normalize_tasks(tasks)
        projects = list({record.project.project_id: record.project for record in records}.values())
        return self._build_collection(records, users, tasks, projects)

    @staticmethod
    def _build_collection(
        records: list[_TimeRecord],
        users: list[DolibarrUserPayload],
        tasks: Iterable[DolibarrTaskPayload],
        projects: Iterable[ProjectDescriptor],
    ) -> _Collection:
        project_map = {project.project_id: project for project in projects}
        task_map = {task.task_id: _task_descriptor(task) for task in tasks}
        for record in records:
            project_map.setdefault(record.project.project_id, record.project)
            task_map.setdefault(record.task.task_id, record.task)
        return _Collection(
            records=records,
            users={user.id: _user_descriptor(user) for user in users},
            projects=project_map,
            tasks=task_map,
        )

    @staticmethod
    def _summary_bucket(
        record: _TimeRecord,
        group_by: SummaryGroup,
        users: dict[int, UserDescriptor],
    ) -> tuple[str, str]:
        if group_by == "user":
            user = users.get(record.user_id, UserDescriptor(user_id=record.user_id))
            return str(record.user_id), _user_label(user)
        if group_by == "project":
            return str(record.project.project_id), _project_label(record.project)
        if group_by == "task":
            return str(record.task.task_id), _task_label(record.task)
        if group_by == "month":
            key = record.day.strftime("%Y-%m")
            return key, key
        if group_by == "week":
            year, week, _weekday = record.day.isocalendar()
            key = f"{year}-W{week:02d}"
            return key, key
        raise ReportRequestError


__all__ = ["MAX_OUTPUT_ROWS", "TimeReportingService"]
