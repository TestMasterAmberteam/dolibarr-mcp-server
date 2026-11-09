"""Preview-confirmed daily time registration for the authenticated user."""

from __future__ import annotations

import hashlib
import hmac
import json
from datetime import UTC, datetime
from typing import TYPE_CHECKING, TypedDict

from dolibarr_mcp.errors import (
    DolibarrError,
    InvalidDolibarrResponseError,
    TaskConfirmationError,
    TaskRequestError,
)
from dolibarr_mcp.models import MutationChange, MutationPreview, MutationResult, TimeEntryDetail

if TYPE_CHECKING:
    from datetime import date

    from dolibarr_mcp.client import DolibarrClient
    from dolibarr_mcp.models import DolibarrTimeEntryPayload, TimeEntryCreateInput, VerifiedIdentity

_CLOSED_PROJECT = 2
_SECONDS_PER_DAY = 86400
_DAILY_WARNING = "Daily entry uses 00:00 UTC; Dolibarr's standard API stores a timed entry."


class _EntryScope(TypedDict):
    task_id: int
    project_id: int
    user_id: int
    work_date: date


def _daily_entries(
    lines: list[DolibarrTimeEntryPayload],
    *,
    task_id: int,
    project_id: int,
    user_id: int,
    work_date: date,
) -> list[DolibarrTimeEntryPayload]:
    start = int(datetime(work_date.year, work_date.month, work_date.day, tzinfo=UTC).timestamp())
    if any(
        line.task_id != task_id or line.project_id not in {None, project_id} for line in lines
    ) or len({line.entry_id for line in lines}) != len(lines):
        raise InvalidDolibarrResponseError
    return sorted(
        (
            line
            for line in lines
            if line.user_id == user_id and start <= line.timestamp < start + _SECONDS_PER_DAY
        ),
        key=lambda line: line.entry_id,
    )


class TimeEntryService:
    """Use the request's verified identity and credential, without retained mutation state."""

    def __init__(self, client: DolibarrClient) -> None:
        self._client = client

    async def create(
        self,
        api_key: str,
        identity: VerifiedIdentity,
        data: TimeEntryCreateInput,
        *,
        apply: bool,
        confirmation_token: str | None,
    ) -> MutationPreview | MutationResult:
        task = await self._client.get_task(api_key, data.task_id)
        if task.task_id != data.task_id:
            raise TaskRequestError
        project = await self._client.get_project(api_key, task.project_id)
        if project.project_id != task.project_id or project.status == _CLOSED_PROJECT:
            raise TaskRequestError
        scope: _EntryScope = {
            "task_id": data.task_id,
            "project_id": task.project_id,
            "user_id": identity.user_id,
            "work_date": data.work_date,
        }
        before = _daily_entries(
            await self._client.get_task_timespent(api_key, data.task_id), **scope
        )
        proposed = {
            "task_id": data.task_id,
            "user_id": identity.user_id,
            "work_date": data.work_date.isoformat(),
            "hours": format(data.hours.normalize(), "f"),
            "duration_seconds": data.duration_seconds,
            "comment": data.comment,
        }
        canonical = json.dumps(
            {
                "operation": "time_entry_create",
                "proposed": proposed,
                "task": task.model_dump(mode="json", exclude={"lines"}),
                "project": project.model_dump(mode="json"),
                "entries": [line.model_dump(mode="json") for line in before],
            },
            sort_keys=True,
            separators=(",", ":"),
        )
        token = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
        warnings = [_DAILY_WARNING]
        warnings.extend(
            f"Possible duplicate time entry {line.entry_id}."
            for line in before
            if line.duration_seconds == data.duration_seconds and (line.note or "") == data.comment
        )
        if not apply:
            return MutationPreview(
                operation="time_entry_create",
                target_kind="task",
                target_id=data.task_id,
                changes=[MutationChange(field=key, after=value) for key, value in proposed.items()],
                warnings=warnings,
                confirmation_token=token,
            )
        if confirmation_token is None or not hmac.compare_digest(token, confirmation_token):
            raise TaskConfirmationError
        errors = []
        try:
            await self._client.create_time_entry(
                api_key,
                task_id=data.task_id,
                work_date=data.work_date.isoformat(),
                duration_seconds=data.duration_seconds,
                comment=data.comment,
            )
        except DolibarrError:
            errors.append("Time entry request failed; inspect recorded time before retrying.")
        detail = None
        try:
            after = _daily_entries(
                await self._client.get_task_timespent(api_key, data.task_id), **scope
            )
            previous_ids = {line.entry_id for line in before}
            matches = [
                line
                for line in after
                if line.entry_id not in previous_ids
                and line.duration_seconds == data.duration_seconds
                and (line.note or "") == data.comment
            ]
            if len(matches) == 1:
                detail = TimeEntryDetail(
                    entry_id=matches[0].entry_id,
                    **scope,
                    duration_seconds=data.duration_seconds,
                    hours=data.hours,
                    comment=data.comment,
                )
            else:
                errors.append(
                    "Could not identify exactly one new matching time entry after reread."
                )
        except DolibarrError:
            errors.append("Could not reread recorded time; write outcome is unverified.")
        return MutationResult(
            operation="time_entry_create",
            outcome="partial" if errors else "applied",
            target_id=data.task_id,
            warnings=warnings,
            partial_errors=errors,
            time_entry=detail,
        )
