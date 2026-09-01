"""Safe API-only workflows for Dolibarr leave requests."""

from __future__ import annotations

import hashlib
import hmac
import json
from datetime import UTC, date, datetime
from typing import TYPE_CHECKING, Literal

from dolibarr_mcp.errors import LeaveConfirmationError, LeaveRequestError
from dolibarr_mcp.models import (
    DolibarrLeaveRequestPayload,
    DolibarrLeaveTypePayload,
    LeaveHalfDayMode,
    LeaveRequestCreateInput,
    LeaveRequestDetail,
    LeaveRequestSearchResult,
    LeaveRequestStatus,
    LeaveRequestSummary,
    LeaveRequestUpdateInput,
    LeaveTypeListResult,
    LeaveTypeSummary,
    MutationChange,
    MutationPreview,
    MutationResult,
)

if TYPE_CHECKING:
    from dolibarr_mcp.client import DolibarrClient

_STATUS_BY_API: dict[int, LeaveRequestStatus] = {
    1: "draft",
    2: "submitted",
    3: "approved",
    4: "canceled",
    5: "refused",
}
_HALF_DAY_BY_API: dict[int, LeaveHalfDayMode] = {
    0: "full_days",
    -1: "start_afternoon_end_afternoon",
    1: "start_morning_end_morning",
    2: "start_afternoon_end_morning",
}
_HALF_DAY_TO_API = {value: key for key, value in _HALF_DAY_BY_API.items()}
_TEXT_LIMIT = 4000
_APPROVAL_WARNING = (
    "Dolibarr remains authoritative for leave balance, overlap, permissions, and "
    "approval policy; approving this request may consume or exceed the employee's balance."
)
_WRITE_WARNING = (
    "Dolibarr validates leave balance, overlap, permissions, configured approver, and "
    "module rules when this operation is applied."
)


def _bounded_text(value: str | None) -> str | None:
    if value is None:
        return None
    return value[:_TEXT_LIMIT]


def _date_to_timestamp(value: date) -> int:
    return int(datetime(value.year, value.month, value.day, tzinfo=UTC).timestamp())


def _timestamp_to_date(value: int) -> date:
    return datetime.fromtimestamp(value, tz=UTC).date()


def _leave_summary(payload: DolibarrLeaveRequestPayload) -> LeaveRequestSummary:
    reference = None if payload.reference is None else str(payload.reference).strip() or None
    return LeaveRequestSummary(
        request_id=payload.request_id,
        reference=reference,
        employee_id=payload.employee_id,
        approver_user_id=payload.approver_user_id,
        leave_type_id=payload.leave_type_id,
        status=_STATUS_BY_API[payload.status_code],
        date_start=_timestamp_to_date(payload.start_timestamp),
        date_end=_timestamp_to_date(payload.end_timestamp),
        half_day_mode=_HALF_DAY_BY_API[payload.half_day_code],
    )


def _leave_detail(payload: DolibarrLeaveRequestPayload) -> LeaveRequestDetail:
    summary = _leave_summary(payload)
    return LeaveRequestDetail(
        **summary.model_dump(),
        description=_bounded_text(payload.description),
        refusal_reason=_bounded_text(payload.refusal_reason),
    )


def _leave_type_summary(payload: DolibarrLeaveTypePayload) -> LeaveTypeSummary:
    return LeaveTypeSummary(
        leave_type_id=payload.type_id,
        code=payload.code,
        label=payload.label,
        affects_balance=payload.affects_balance,
        block_if_negative=payload.block_if_negative,
    )


def _changes(
    current: dict[str, object],
    proposed: dict[str, object],
) -> list[MutationChange]:
    return [
        MutationChange(field=field, before=current.get(field), after=value)
        for field, value in proposed.items()
        if current.get(field) != value
    ]


def _create_changes(proposed: dict[str, object]) -> list[MutationChange]:
    return [MutationChange(field=field, after=value) for field, value in proposed.items()]


def _confirmation_token(
    *,
    operation: str,
    target_id: int | None,
    proposed: dict[str, object],
    current: dict[str, object] | None,
) -> str:
    canonical = json.dumps(
        {
            "operation": operation,
            "target_id": target_id,
            "proposed": proposed,
            "current": current,
        },
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _confirmed(preview: MutationPreview, *, apply: bool, token: str | None) -> bool:
    if not apply:
        return False
    if token is None or not hmac.compare_digest(preview.confirmation_token, token):
        raise LeaveConfirmationError
    return True


def _api_payload(
    data: LeaveRequestCreateInput | LeaveRequestUpdateInput,
) -> dict[str, object]:
    source = data.model_dump(exclude_none=True)
    payload: dict[str, object] = {}
    field_mapping = {
        "employee_id": "fk_user",
        "leave_type_id": "fk_type",
        "approver_user_id": "fk_validator",
        "description": "description",
    }
    for field, api_field in field_mapping.items():
        if field in source:
            payload[api_field] = source[field]
    for field, api_field in (("date_start", "date_debut"), ("date_end", "date_fin")):
        value = getattr(data, field)
        if value is not None:
            payload[api_field] = _date_to_timestamp(value)
    if data.half_day_mode is not None:
        payload["halfday"] = _HALF_DAY_TO_API[data.half_day_mode]
    return payload


class LeaveRequestService:
    """Read and mutate leave requests without storing request or credential state."""

    def __init__(self, client: DolibarrClient) -> None:
        self._client = client

    async def leave_type_list(self, api_key: str) -> LeaveTypeListResult:
        rows = sorted(
            await self._client.list_leave_types(api_key),
            key=lambda item: (item.sort_order, item.type_id),
        )
        summaries = [_leave_type_summary(row) for row in rows if row.active]
        return LeaveTypeListResult(count=len(summaries), rows=summaries)

    async def search(
        self,
        api_key: str,
        *,
        employee_id: int | None,
        status: LeaveRequestStatus | None,
        date_from: date | None,
        date_to: date | None,
        offset: int,
        limit: int,
    ) -> LeaveRequestSearchResult:
        if date_from is not None and date_to is not None and date_from > date_to:
            raise LeaveRequestError
        summaries = [
            _leave_summary(row)
            for row in await self._client.list_leave_requests(
                api_key,
                employee_id=employee_id,
            )
        ]
        filtered = [
            row
            for row in summaries
            if (status is None or row.status == status)
            and (date_from is None or row.date_end >= date_from)
            and (date_to is None or row.date_start <= date_to)
        ]
        filtered.sort(key=lambda row: (row.date_start, row.request_id))
        selected = filtered[offset : offset + limit]
        return LeaveRequestSearchResult(
            total_count=len(filtered),
            returned_count=len(selected),
            offset=offset,
            limit=limit,
            has_more=offset + len(selected) < len(filtered),
            rows=selected,
        )

    async def get(self, api_key: str, request_id: int) -> LeaveRequestDetail:
        return _leave_detail(await self._client.get_leave_request(api_key, request_id))

    async def create(
        self,
        api_key: str,
        data: LeaveRequestCreateInput,
        *,
        apply: bool,
        confirmation_token: str | None,
    ) -> MutationPreview | MutationResult:
        proposed = data.model_dump(mode="json", exclude_none=True)
        preview = MutationPreview(
            operation="leave_request_create",
            target_kind="leave_request",
            changes=_create_changes(proposed),
            warnings=[_WRITE_WARNING],
            confirmation_token=_confirmation_token(
                operation="leave_request_create",
                target_id=None,
                proposed=proposed,
                current=None,
            ),
        )
        if not _confirmed(preview, apply=apply, token=confirmation_token):
            return preview
        request_id = await self._client.create_leave_request(api_key, _api_payload(data))
        detail = await self.get(api_key, request_id)
        return MutationResult(
            operation=preview.operation,
            outcome="applied",
            target_id=request_id,
            warnings=preview.warnings,
            leave_request=detail,
        )

    async def update(
        self,
        api_key: str,
        request_id: int,
        data: LeaveRequestUpdateInput,
        *,
        apply: bool,
        confirmation_token: str | None,
    ) -> MutationPreview | MutationResult:
        current = await self.get(api_key, request_id)
        if current.status != "draft":
            raise LeaveRequestError
        date_start = data.date_start or current.date_start
        date_end = data.date_end or current.date_end
        if date_start > date_end:
            raise LeaveRequestError
        proposed = data.model_dump(mode="json", exclude_none=True)
        current_data = current.model_dump(mode="json")
        changes = _changes(current_data, proposed)
        preview = MutationPreview(
            operation="leave_request_update",
            target_kind="leave_request",
            target_id=request_id,
            changes=changes,
            warnings=[_WRITE_WARNING],
            confirmation_token=_confirmation_token(
                operation="leave_request_update",
                target_id=request_id,
                proposed=proposed,
                current=current_data,
            ),
        )
        if not _confirmed(preview, apply=apply, token=confirmation_token):
            return preview
        if not changes:
            return MutationResult(
                operation=preview.operation,
                outcome="no_op",
                target_id=request_id,
                warnings=preview.warnings,
                leave_request=current,
            )
        await self._client.update_leave_request(api_key, request_id, _api_payload(data))
        return MutationResult(
            operation=preview.operation,
            outcome="applied",
            target_id=request_id,
            warnings=preview.warnings,
            leave_request=await self.get(api_key, request_id),
        )

    async def submit(
        self,
        api_key: str,
        request_id: int,
        *,
        apply: bool,
        confirmation_token: str | None,
    ) -> MutationPreview | MutationResult:
        return await self._transition(
            api_key,
            request_id,
            action="submit",
            apply=apply,
            confirmation_token=confirmation_token,
        )

    async def approve(
        self,
        api_key: str,
        request_id: int,
        *,
        apply: bool,
        confirmation_token: str | None,
    ) -> MutationPreview | MutationResult:
        return await self._transition(
            api_key,
            request_id,
            action="approve",
            apply=apply,
            confirmation_token=confirmation_token,
        )

    async def refuse(
        self,
        api_key: str,
        request_id: int,
        refusal_reason: str,
        *,
        apply: bool,
        confirmation_token: str | None,
    ) -> MutationPreview | MutationResult:
        normalized_reason = refusal_reason.strip()
        if not normalized_reason or len(normalized_reason) > _TEXT_LIMIT:
            raise LeaveRequestError
        return await self._transition(
            api_key,
            request_id,
            action="refuse",
            refusal_reason=normalized_reason,
            apply=apply,
            confirmation_token=confirmation_token,
        )

    async def cancel(
        self,
        api_key: str,
        request_id: int,
        *,
        apply: bool,
        confirmation_token: str | None,
    ) -> MutationPreview | MutationResult:
        return await self._transition(
            api_key,
            request_id,
            action="cancel",
            apply=apply,
            confirmation_token=confirmation_token,
        )

    async def reopen(
        self,
        api_key: str,
        request_id: int,
        *,
        apply: bool,
        confirmation_token: str | None,
    ) -> MutationPreview | MutationResult:
        return await self._transition(
            api_key,
            request_id,
            action="reopen",
            apply=apply,
            confirmation_token=confirmation_token,
        )

    async def _transition(
        self,
        api_key: str,
        request_id: int,
        *,
        action: Literal["submit", "approve", "refuse", "cancel", "reopen"],
        apply: bool,
        confirmation_token: str | None,
        refusal_reason: str | None = None,
    ) -> MutationPreview | MutationResult:
        desired_by_action: dict[
            Literal["submit", "approve", "refuse", "cancel", "reopen"],
            LeaveRequestStatus,
        ] = {
            "submit": "submitted",
            "approve": "approved",
            "refuse": "refused",
            "cancel": "canceled",
            "reopen": "submitted",
        }
        allowed_by_action: dict[str, set[LeaveRequestStatus]] = {
            "submit": {"draft"},
            "approve": {"submitted"},
            "refuse": {"submitted"},
            "cancel": {"submitted", "approved"},
            "reopen": {"canceled"},
        }
        current = await self.get(api_key, request_id)
        desired = desired_by_action[action]
        if current.status != desired and current.status not in allowed_by_action[action]:
            raise LeaveRequestError
        proposed: dict[str, object] = {"status": desired}
        if refusal_reason is not None and current.status != desired:
            proposed["refusal_reason"] = refusal_reason
        current_data = current.model_dump(mode="json")
        changes = _changes(current_data, proposed)
        warnings = [_APPROVAL_WARNING] if action == "approve" else []
        operation = f"leave_request_{action}"
        preview = MutationPreview(
            operation=operation,
            target_kind="leave_request",
            target_id=request_id,
            changes=changes,
            warnings=warnings,
            confirmation_token=_confirmation_token(
                operation=operation,
                target_id=request_id,
                proposed=proposed,
                current=current_data,
            ),
        )
        if not _confirmed(preview, apply=apply, token=confirmation_token):
            return preview
        if not changes:
            return MutationResult(
                operation=operation,
                outcome="no_op",
                target_id=request_id,
                warnings=warnings,
                leave_request=current,
            )
        if action == "submit":
            await self._client.submit_leave_request(api_key, request_id)
        elif action == "approve":
            await self._client.approve_leave_request(api_key, request_id)
        elif action == "refuse":
            if refusal_reason is None:
                raise LeaveRequestError
            await self._client.refuse_leave_request(api_key, request_id, refusal_reason)
        elif action == "cancel":
            await self._client.cancel_leave_request(api_key, request_id)
        else:
            await self._client.reopen_leave_request(api_key, request_id)
        updated = await self.get(api_key, request_id)
        if updated.status != desired:
            return MutationResult(
                operation=operation,
                outcome="partial",
                target_id=request_id,
                warnings=warnings,
                partial_errors=["Dolibarr did not persist the expected leave-request status."],
                leave_request=updated,
            )
        return MutationResult(
            operation=operation,
            outcome="applied",
            target_id=request_id,
            warnings=warnings,
            leave_request=updated,
        )
