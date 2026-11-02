"""Stateless preview-confirmed creation and assignment of project tasks."""

from __future__ import annotations

import hashlib
import hmac
import json
from datetime import UTC, date, datetime
from typing import TYPE_CHECKING

from dolibarr_mcp.errors import DolibarrError, TaskConfirmationError, TaskRequestError
from dolibarr_mcp.models import (
    MutationChange,
    MutationPreview,
    MutationResult,
    TaskAssignmentDetail,
    TaskCreateInput,
    TaskDetail,
    TaskRole,
    UserSummary,
)

if TYPE_CHECKING:
    from dolibarr_mcp.client import DolibarrClient
    from dolibarr_mcp.models import DolibarrTaskContactPayload, DolibarrTaskDetailPayload

_CLOSED_PROJECT_STATUS = 2
_ACTIVE_CONTACT_STATUS = 4


def _timestamp(value: date) -> int:
    return int(datetime(value.year, value.month, value.day, 12, tzinfo=UTC).timestamp())


def _detail(saved: DolibarrTaskDetailPayload) -> TaskDetail:
    return TaskDetail(
        task_id=saved.task_id,
        ref=saved.ref,
        project_id=saved.project_id,
        label=saved.label,
        parent_task_id=saved.parent_task_id,
        description=saved.description,
        planned_workload_seconds=saved.planned_workload_seconds,
        date_start=datetime.fromtimestamp(saved.date_start, tz=UTC).date()
        if saved.date_start is not None
        else None,
        date_end=datetime.fromtimestamp(saved.date_end, tz=UTC).date()
        if saved.date_end is not None
        else None,
    )


def _assigned(contacts: list[DolibarrTaskContactPayload], user_id: int, role: TaskRole) -> bool:
    return any(
        contact.source == "internal"
        and contact.contact_id == user_id
        and contact.code == role
        and contact.status == _ACTIVE_CONTACT_STATUS
        for contact in contacts
    )


class TaskService:
    """Use the request credential for every read and the single confirmed POST."""

    def __init__(self, client: DolibarrClient) -> None:
        self._client = client

    async def assign(
        self,
        api_key: str,
        *,
        project_id: int,
        task_id: int,
        user_id: int,
        role: TaskRole,
        apply: bool,
        confirmation_token: str | None,
    ) -> MutationPreview | MutationResult:
        task = await self._client.get_task(api_key, task_id)
        if task.task_id != task_id or task.project_id != project_id:
            raise TaskRequestError
        project = await self._client.get_project(api_key, project_id)
        if project.project_id != project_id or project.status == _CLOSED_PROJECT_STATUS:
            raise TaskRequestError
        user = await self._client.get_user(api_key, user_id)
        if user.id != user_id or user.status != 1:
            raise TaskRequestError
        contacts = await self._client.get_task_contacts(api_key, task_id)
        exists = _assigned(contacts, user_id, role)
        proposed = {"project_id": project_id, "task_id": task_id, "user_id": user_id, "role": role}
        current = {
            "task": task.model_dump(mode="json"),
            "project": project.model_dump(mode="json"),
            "user": user.model_dump(mode="json"),
            "contacts": [
                contact.model_dump(mode="json")
                for contact in sorted(contacts, key=lambda contact: contact.row_id)
            ],
        }
        canonical = json.dumps(
            {"operation": "task_assign", "proposed": proposed, "current": current},
            sort_keys=True,
            separators=(",", ":"),
        )
        token = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
        preview = MutationPreview(
            operation="task_assign",
            target_kind="task",
            target_id=task_id,
            changes=[]
            if exists
            else [
                MutationChange(field="user_id", after=user_id),
                MutationChange(field="role", after=role),
            ],
            warnings=["User already has this active internal task role."] if exists else [],
            confirmation_token=token,
        )
        if not apply:
            return preview
        if confirmation_token is None or not hmac.compare_digest(token, confirmation_token):
            raise TaskConfirmationError
        assignment = TaskAssignmentDetail(
            project_id=project_id,
            task_id=task_id,
            user=UserSummary(
                user_id=user.id,
                login=user.login,
                first_name=user.first_name,
                last_name=user.last_name,
            ),
            role=role,
            assigned=True if exists else None,
        )
        if exists:
            return MutationResult(
                operation="task_assign",
                outcome="no_op",
                target_id=task_id,
                warnings=preview.warnings,
                task_assignment=assignment,
            )
        errors = []
        try:
            await self._client.add_task_user(api_key, task_id, user_id, role)
        except DolibarrError:
            errors.append(
                "Task assignment request failed; inspect current assignments before retrying."
            )
        try:
            refreshed = await self._client.get_task_contacts(api_key, task_id)
            assigned = _assigned(refreshed, user_id, role)
            assignment = assignment.model_copy(update={"assigned": assigned})
            if not assigned:
                errors.append("Requested active internal task role is not visible after reread.")
        except DolibarrError:
            errors.append("Could not reread task assignments; write outcome is unverified.")
        return MutationResult(
            operation="task_assign",
            outcome="partial" if errors else "applied",
            target_id=task_id,
            partial_errors=errors,
            task_assignment=assignment,
        )

    async def create(
        self,
        api_key: str,
        data: TaskCreateInput,
        *,
        apply: bool,
        confirmation_token: str | None,
    ) -> MutationPreview | MutationResult:
        project = await self._client.get_project(api_key, data.project_id)
        if project.project_id != data.project_id or project.status == _CLOSED_PROJECT_STATUS:
            raise TaskRequestError
        parent = None
        if data.parent_task_id is not None:
            parent = await self._client.get_task(api_key, data.parent_task_id)
            if parent.project_id != data.project_id or parent.task_id != data.parent_task_id:
                raise TaskRequestError
        tasks = await self._client.get_project_task_metadata(api_key, data.project_id)
        duplicates = sorted(
            task.task_id
            for task in tasks
            if task.project_id == data.project_id
            and " ".join(task.label.casefold().split()) == " ".join(data.label.casefold().split())
        )
        proposed = data.model_dump(mode="json", exclude_none=True)
        canonical = json.dumps(
            {
                "operation": "task_create",
                "proposed": proposed,
                "project": project.model_dump(mode="json"),
                "parent": parent.model_dump(mode="json") if parent else None,
                "duplicates": duplicates,
            },
            sort_keys=True,
            separators=(",", ":"),
        )
        token = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
        warnings = [f"Possible duplicate task {task_id}." for task_id in duplicates]
        preview = MutationPreview(
            operation="task_create",
            target_kind="task",
            changes=[MutationChange(field=key, after=value) for key, value in proposed.items()],
            warnings=warnings,
            confirmation_token=token,
        )
        if not apply:
            return preview
        if confirmation_token is None or not hmac.compare_digest(token, confirmation_token):
            raise TaskConfirmationError
        payload: dict[str, object] = {
            "ref": "auto",
            "fk_project": data.project_id,
            "label": data.label,
            "fk_task_parent": data.parent_task_id or 0,
        }
        if data.description is not None:
            payload["description"] = data.description
        if data.planned_workload_seconds is not None:
            payload["planned_workload"] = data.planned_workload_seconds
        for field in ("date_start", "date_end"):
            value = getattr(data, field)
            if value is not None:
                payload[field] = _timestamp(value)
        task_id = await self._client.create_task(api_key, payload)
        try:
            saved = await self._client.get_task_detail(api_key, task_id)
            detail = _detail(saved)
        except (DolibarrError, ValueError, OverflowError, OSError):
            return MutationResult(
                operation="task_create",
                outcome="partial",
                target_id=task_id,
                warnings=warnings,
                partial_errors=["Task was created but could not be reread; do not retry creation."],
            )
        actual = detail.model_dump(mode="json")
        expected = {**proposed, "parent_task_id": data.parent_task_id, "task_id": task_id}
        matches = all(actual.get(key) == value for key, value in expected.items())
        return MutationResult(
            operation="task_create",
            outcome="applied" if matches else "partial",
            target_id=task_id,
            warnings=warnings,
            task=detail,
            partial_errors=[] if matches else ["Reread task differs from the confirmed values."],
        )
