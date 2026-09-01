"""Leave-request service behavior over a stateful API-shaped client double."""

# ruff: noqa: ARG002

from __future__ import annotations

from datetime import UTC, date, datetime
from typing import TYPE_CHECKING, cast

import pytest
from pydantic import ValidationError

from dolibarr_mcp.errors import LeaveConfirmationError, LeaveRequestError
from dolibarr_mcp.leave_requests import LeaveRequestService
from dolibarr_mcp.models import (
    DolibarrLeaveRequestPayload,
    DolibarrLeaveTypePayload,
    LeaveRequestCreateInput,
    LeaveRequestUpdateInput,
    MutationPreview,
    MutationResult,
)

if TYPE_CHECKING:
    from dolibarr_mcp.client import DolibarrClient

pytestmark = pytest.mark.anyio


def timestamp(year: int, month: int, day: int) -> int:
    return int(datetime(year, month, day, tzinfo=UTC).timestamp())


def leave_request(
    request_id: int,
    *,
    status: int,
    employee_id: int = 7,
    date_start: date = date(2026, 8, 14),
    date_end: date = date(2026, 8, 14),
    halfday: int = 0,
    description: str | None = "Holiday",
) -> DolibarrLeaveRequestPayload:
    return DolibarrLeaveRequestPayload.model_validate(
        {
            "id": request_id,
            "ref": request_id if request_id != 5 else "",
            "fk_user": employee_id,
            "fk_validator": "8" if request_id != 5 else "",
            "fk_type": 2,
            "date_debut": timestamp(date_start.year, date_start.month, date_start.day),
            "date_fin": timestamp(date_end.year, date_end.month, date_end.day),
            "halfday": halfday,
            "statut": status,
            "description": description,
            "detail_refuse": "No capacity" if status == 5 else "",
            "secret": "must-not-escape",
        }
    )


class FakeLeaveClient:
    """Stateful double whose methods correspond to fixed Holidays API calls."""

    def __init__(self) -> None:
        self.requests = {
            1: leave_request(1, status=1, description="x" * 4000),
            2: leave_request(
                2,
                status=2,
                employee_id=9,
                date_start=date(2026, 8, 15),
                date_end=date(2026, 8, 16),
                halfday=2,
            ),
            3: leave_request(3, status=3),
            4: leave_request(4, status=4),
            5: leave_request(5, status=5, description=None),
        }
        self.types = [
            DolibarrLeaveTypePayload.model_validate(
                {
                    "rowid": 2,
                    "code": "PAID",
                    "label": "Paid leave",
                    "active": 1,
                    "affect": 1,
                    "block_if_negative": 1,
                    "sortorder": 20,
                }
            ),
            DolibarrLeaveTypePayload.model_validate(
                {
                    "id": 1,
                    "code": "UNPAID",
                    "label": "Unpaid leave",
                    "active": 1,
                    "sort_order": 10,
                }
            ),
            DolibarrLeaveTypePayload.model_validate(
                {"id": 3, "code": "OLD", "label": "Inactive", "active": 0}
            ),
        ]
        self.calls: list[tuple[str, object]] = []
        self.no_effect_action: str | None = None

    async def list_leave_types(self, api_key: str) -> list[DolibarrLeaveTypePayload]:
        return list(self.types)

    async def list_leave_requests(
        self,
        api_key: str,
        *,
        employee_id: int | None = None,
    ) -> list[DolibarrLeaveRequestPayload]:
        rows = list(self.requests.values())
        if employee_id is not None:
            rows = [row for row in rows if row.employee_id == employee_id]
        return rows

    async def get_leave_request(
        self,
        api_key: str,
        request_id: int,
    ) -> DolibarrLeaveRequestPayload:
        return self.requests[request_id]

    async def create_leave_request(self, api_key: str, payload: dict[str, object]) -> int:
        self.calls.append(("create", payload))
        request_id = max(self.requests) + 1
        self.requests[request_id] = DolibarrLeaveRequestPayload.model_validate(
            {"id": request_id, "ref": f"LR-{request_id}", "status": 1, **payload}
        )
        return request_id

    async def update_leave_request(
        self,
        api_key: str,
        request_id: int,
        payload: dict[str, object],
    ) -> DolibarrLeaveRequestPayload:
        self.calls.append(("update", payload))
        mapping = {
            "fk_user": "employee_id",
            "fk_type": "leave_type_id",
            "fk_validator": "approver_user_id",
            "date_debut": "start_timestamp",
            "date_fin": "end_timestamp",
            "halfday": "half_day_code",
            "description": "description",
        }
        updates = {mapping[key]: value for key, value in payload.items()}
        result = self.requests[request_id].model_copy(update=updates)
        self.requests[request_id] = result
        return result

    async def submit_leave_request(
        self, api_key: str, request_id: int
    ) -> DolibarrLeaveRequestPayload:
        return self._set_status("submit", request_id, 2)

    async def approve_leave_request(
        self, api_key: str, request_id: int
    ) -> DolibarrLeaveRequestPayload:
        return self._set_status("approve", request_id, 3)

    async def refuse_leave_request(
        self,
        api_key: str,
        request_id: int,
        refusal_reason: str,
    ) -> DolibarrLeaveRequestPayload:
        self.calls.append(("refuse_reason", refusal_reason))
        result = self._set_status("refuse", request_id, 5)
        result = result.model_copy(update={"refusal_reason": refusal_reason})
        self.requests[request_id] = result
        return result

    async def cancel_leave_request(
        self, api_key: str, request_id: int
    ) -> DolibarrLeaveRequestPayload:
        return self._set_status("cancel", request_id, 4)

    async def reopen_leave_request(
        self, api_key: str, request_id: int
    ) -> DolibarrLeaveRequestPayload:
        return self._set_status("reopen", request_id, 2)

    def _set_status(
        self,
        action: str,
        request_id: int,
        status: int,
    ) -> DolibarrLeaveRequestPayload:
        self.calls.append((action, request_id))
        current = self.requests[request_id]
        if self.no_effect_action == action:
            return current
        result = current.model_copy(update={"status_code": status})
        self.requests[request_id] = result
        return result


def service(fake: FakeLeaveClient) -> LeaveRequestService:
    return LeaveRequestService(cast("DolibarrClient", fake))


async def test_read_tools_are_bounded_allowlisted_and_filter_locally() -> None:
    fake = FakeLeaveClient()
    leaves = service(fake)

    types = await leaves.leave_type_list("key")
    assert [row.leave_type_id for row in types.rows] == [1, 2]
    assert types.rows[1].affects_balance is True
    assert types.rows[1].block_if_negative is True

    result = await leaves.search(
        "key",
        employee_id=None,
        status="submitted",
        date_from=date(2026, 8, 16),
        date_to=date(2026, 8, 20),
        offset=0,
        limit=1,
    )
    assert result.total_count == 1
    assert result.returned_count == 1
    assert result.has_more is False
    assert result.rows[0].request_id == 2
    assert result.rows[0].half_day_mode == "start_afternoon_end_morning"

    employee = await leaves.search(
        "key",
        employee_id=7,
        status=None,
        date_from=None,
        date_to=None,
        offset=1,
        limit=2,
    )
    assert employee.total_count == 4
    assert employee.has_more is True

    detail = await leaves.get("key", 1)
    assert detail.reference == "1"
    assert len(detail.description or "") == 4000
    assert (await leaves.get("key", 5)).reference is None
    assert (await leaves.get("key", 5)).description is None

    with pytest.raises(LeaveRequestError):
        await leaves.search(
            "key",
            employee_id=None,
            status=None,
            date_from=date(2026, 9, 1),
            date_to=date(2026, 8, 1),
            offset=0,
            limit=10,
        )


async def test_create_requires_current_preview_token_and_maps_half_days() -> None:
    fake = FakeLeaveClient()
    leaves = service(fake)
    data = LeaveRequestCreateInput(
        employee_id=11,
        leave_type_id=2,
        date_start=date(2026, 9, 1),
        date_end=date(2026, 9, 2),
        half_day_mode="start_afternoon_end_afternoon",
        approver_user_id=8,
        description="Conference",
    )

    preview = await leaves.create("key", data, apply=False, confirmation_token=None)
    assert isinstance(preview, MutationPreview)
    assert preview.target_id is None
    assert preview.warnings
    with pytest.raises(LeaveConfirmationError):
        await leaves.create("key", data, apply=True, confirmation_token="0" * 64)
    assert fake.calls == []

    applied = await leaves.create(
        "key",
        data,
        apply=True,
        confirmation_token=preview.confirmation_token,
    )
    assert isinstance(applied, MutationResult)
    assert applied.outcome == "applied"
    assert applied.leave_request is not None
    assert applied.leave_request.status == "draft"
    assert fake.calls == [
        (
            "create",
            {
                "fk_user": 11,
                "fk_type": 2,
                "fk_validator": 8,
                "description": "Conference",
                "date_debut": timestamp(2026, 9, 1),
                "date_fin": timestamp(2026, 9, 2),
                "halfday": -1,
            },
        )
    ]


async def test_update_rejects_non_drafts_stale_tokens_and_bad_merged_dates() -> None:
    fake = FakeLeaveClient()
    leaves = service(fake)
    data = LeaveRequestUpdateInput(description="Changed")

    preview = await leaves.update("key", 1, data, apply=False, confirmation_token=None)
    assert isinstance(preview, MutationPreview)
    fake.requests[1] = fake.requests[1].model_copy(update={"description": "Concurrent"})
    with pytest.raises(LeaveConfirmationError):
        await leaves.update(
            "key",
            1,
            data,
            apply=True,
            confirmation_token=preview.confirmation_token,
        )

    fresh = await leaves.update("key", 1, data, apply=False, confirmation_token=None)
    assert isinstance(fresh, MutationPreview)
    applied = await leaves.update(
        "key",
        1,
        data,
        apply=True,
        confirmation_token=fresh.confirmation_token,
    )
    assert isinstance(applied, MutationResult)
    assert applied.outcome == "applied"
    assert ("update", {"description": "Changed"}) in fake.calls

    same = LeaveRequestUpdateInput(description="Changed")
    no_op_preview = await leaves.update("key", 1, same, apply=False, confirmation_token=None)
    assert isinstance(no_op_preview, MutationPreview)
    no_op = await leaves.update(
        "key",
        1,
        same,
        apply=True,
        confirmation_token=no_op_preview.confirmation_token,
    )
    assert isinstance(no_op, MutationResult)
    assert no_op.outcome == "no_op"

    with pytest.raises(LeaveRequestError):
        await leaves.update(
            "key",
            2,
            data,
            apply=False,
            confirmation_token=None,
        )
    with pytest.raises(LeaveRequestError):
        await leaves.update(
            "key",
            1,
            LeaveRequestUpdateInput(date_start=date(2026, 9, 1)),
            apply=False,
            confirmation_token=None,
        )


async def test_all_transitions_are_confirmed_and_status_constrained() -> None:
    fake = FakeLeaveClient()
    leaves = service(fake)

    submitted_preview = await leaves.submit("key", 1, apply=False, confirmation_token=None)
    assert isinstance(submitted_preview, MutationPreview)
    submitted = await leaves.submit(
        "key",
        1,
        apply=True,
        confirmation_token=submitted_preview.confirmation_token,
    )
    assert isinstance(submitted, MutationResult)
    assert submitted.leave_request is not None
    assert submitted.leave_request.status == "submitted"

    no_op_preview = await leaves.submit("key", 1, apply=False, confirmation_token=None)
    assert isinstance(no_op_preview, MutationPreview)
    no_op = await leaves.submit(
        "key",
        1,
        apply=True,
        confirmation_token=no_op_preview.confirmation_token,
    )
    assert isinstance(no_op, MutationResult)
    assert no_op.outcome == "no_op"

    approve_preview = await leaves.approve("key", 2, apply=False, confirmation_token=None)
    assert isinstance(approve_preview, MutationPreview)
    assert "balance" in approve_preview.warnings[0]
    approved = await leaves.approve(
        "key",
        2,
        apply=True,
        confirmation_token=approve_preview.confirmation_token,
    )
    assert isinstance(approved, MutationResult)
    assert approved.outcome == "applied"

    refuse_preview = await leaves.refuse(
        "key", 1, "  Insufficient coverage  ", apply=False, confirmation_token=None
    )
    assert isinstance(refuse_preview, MutationPreview)
    refused = await leaves.refuse(
        "key",
        1,
        "  Insufficient coverage  ",
        apply=True,
        confirmation_token=refuse_preview.confirmation_token,
    )
    assert isinstance(refused, MutationResult)
    assert ("refuse_reason", "Insufficient coverage") in fake.calls

    cancel_preview = await leaves.cancel("key", 3, apply=False, confirmation_token=None)
    assert isinstance(cancel_preview, MutationPreview)
    canceled = await leaves.cancel(
        "key",
        3,
        apply=True,
        confirmation_token=cancel_preview.confirmation_token,
    )
    assert isinstance(canceled, MutationResult)
    assert canceled.leave_request is not None
    assert canceled.leave_request.status == "canceled"

    reopen_preview = await leaves.reopen("key", 4, apply=False, confirmation_token=None)
    assert isinstance(reopen_preview, MutationPreview)
    reopened = await leaves.reopen(
        "key",
        4,
        apply=True,
        confirmation_token=reopen_preview.confirmation_token,
    )
    assert isinstance(reopened, MutationResult)
    assert reopened.leave_request is not None
    assert reopened.leave_request.status == "submitted"

    already_refused_preview = await leaves.refuse(
        "key", 5, "Different reason", apply=False, confirmation_token=None
    )
    assert isinstance(already_refused_preview, MutationPreview)
    assert already_refused_preview.changes == []
    already_refused = await leaves.refuse(
        "key",
        5,
        "Different reason",
        apply=True,
        confirmation_token=already_refused_preview.confirmation_token,
    )
    assert isinstance(already_refused, MutationResult)
    assert already_refused.outcome == "no_op"

    with pytest.raises(LeaveRequestError):
        await leaves.approve("key", 5, apply=False, confirmation_token=None)
    with pytest.raises(LeaveRequestError):
        await leaves.refuse("key", 4, "   ", apply=False, confirmation_token=None)


async def test_transition_detects_stale_token_and_unpersisted_status() -> None:
    fake = FakeLeaveClient()
    leaves = service(fake)

    preview = await leaves.approve("key", 2, apply=False, confirmation_token=None)
    assert isinstance(preview, MutationPreview)
    fake.requests[2] = fake.requests[2].model_copy(update={"description": "Concurrent"})
    with pytest.raises(LeaveConfirmationError):
        await leaves.approve(
            "key",
            2,
            apply=True,
            confirmation_token=preview.confirmation_token,
        )

    fresh = await leaves.approve("key", 2, apply=False, confirmation_token=None)
    assert isinstance(fresh, MutationPreview)
    fake.no_effect_action = "approve"
    partial = await leaves.approve(
        "key",
        2,
        apply=True,
        confirmation_token=fresh.confirmation_token,
    )
    assert isinstance(partial, MutationResult)
    assert partial.outcome == "partial"
    assert partial.partial_errors


def test_leave_input_models_reject_unknown_empty_and_invalid_values() -> None:
    with pytest.raises(ValidationError):
        LeaveRequestCreateInput(
            employee_id=1,
            leave_type_id=2,
            date_start=date(2026, 9, 2),
            date_end=date(2026, 9, 1),
        )
    with pytest.raises(ValidationError):
        LeaveRequestUpdateInput()
    with pytest.raises(ValidationError):
        LeaveRequestUpdateInput.model_validate({"unknown": "field"})
    with pytest.raises(ValidationError):
        LeaveRequestUpdateInput(
            date_start=date(2026, 9, 2),
            date_end=date(2026, 9, 1),
        )
