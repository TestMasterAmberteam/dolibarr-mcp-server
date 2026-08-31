"""API-only sales operations for Dolibarr third parties and project leads."""

from __future__ import annotations

import hashlib
import hmac
import json
import re
from datetime import UTC, date, datetime
from typing import TYPE_CHECKING

from dolibarr_mcp.errors import (
    DolibarrError,
    DolibarrNotFoundError,
    SalesConfirmationError,
)
from dolibarr_mcp.models import (
    CustomerStatus,
    DolibarrProjectContactPayload,
    DolibarrProjectPayload,
    DolibarrThirdpartyPayload,
    DolibarrUserPayload,
    LeadCreateInput,
    LeadDetail,
    LeadSearchResult,
    LeadSummary,
    LeadUpdateInput,
    MutationChange,
    MutationPreview,
    MutationResult,
    ProjectState,
    ThirdpartyCreateInput,
    ThirdpartyDetail,
    ThirdpartySearchResult,
    ThirdpartySummary,
    ThirdpartyUpdateInput,
    UserSearchResult,
    UserSummary,
)

if TYPE_CHECKING:
    from dolibarr_mcp.client import DolibarrClient

_CUSTOMER_STATUS_TO_API: dict[CustomerStatus, int] = {
    "neutral": 0,
    "customer": 1,
    "prospect": 2,
    "customer_and_prospect": 3,
}
_API_TO_CUSTOMER_STATUS: dict[int, CustomerStatus] = {
    value: key for key, value in _CUSTOMER_STATUS_TO_API.items()
}
_PROJECT_STATE_BY_API: dict[int, ProjectState] = {0: "draft", 1: "open", 2: "closed"}
_LEADER_CODE = "PROJECTLEADER"
_NOTE_LIMIT = 4000


def _normalize_text(value: str | None) -> str:
    if value is None:
        return ""
    return " ".join(value.casefold().split())


def _normalize_vat(value: str | None) -> str:
    return re.sub(r"[^0-9a-z]", "", _normalize_text(value))


def _bounded_note(value: str | None) -> str | None:
    if value is None:
        return None
    return value[:_NOTE_LIMIT]


def _date_to_timestamp(value: date) -> int:
    """Use UTC noon so a date remains stable across ordinary server time zones."""
    return int(datetime(value.year, value.month, value.day, 12, tzinfo=UTC).timestamp())


def _timestamp_to_date(value: int | None) -> date | None:
    if value is None:
        return None
    return datetime.fromtimestamp(value, tz=UTC).date()


def _confirmation_token(
    *,
    operation: str,
    target_id: int | None,
    proposed: dict[str, object],
    current: dict[str, object] | None,
    duplicate_ids: list[int] | None = None,
) -> str:
    canonical = json.dumps(
        {
            "operation": operation,
            "target_id": target_id,
            "proposed": proposed,
            "current": current,
            "duplicate_ids": sorted(duplicate_ids or []),
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
        raise SalesConfirmationError
    return True


def _thirdparty_summary(payload: DolibarrThirdpartyPayload) -> ThirdpartySummary:
    return ThirdpartySummary(
        thirdparty_id=payload.thirdparty_id,
        name=payload.name,
        alias=payload.alias,
        city=payload.city,
        country_code=payload.country_code,
        email=payload.email,
        vat_number=payload.vat_number,
        customer_status=_API_TO_CUSTOMER_STATUS[payload.customer_classification],
    )


def _thirdparty_detail(payload: DolibarrThirdpartyPayload) -> ThirdpartyDetail:
    summary = _thirdparty_summary(payload)
    return ThirdpartyDetail(
        **summary.model_dump(),
        address=payload.address,
        postal_code=payload.postal_code,
        phone=payload.phone,
        public_note=_bounded_note(payload.public_note),
        private_note=_bounded_note(payload.private_note),
    )


def _user_summary_from_payload(payload: DolibarrUserPayload) -> UserSummary:
    return UserSummary(
        user_id=payload.id,
        login=payload.login,
        first_name=payload.first_name,
        last_name=payload.last_name,
    )


def _is_internal_leader(contact: DolibarrProjectContactPayload) -> bool:
    if contact.code.casefold() != _LEADER_CODE.casefold():
        return False
    source = (contact.source or "").casefold()
    return source == "internal" or (not source and contact.login is not None)


def _user_summary_from_contact(contact: DolibarrProjectContactPayload) -> UserSummary:
    return UserSummary(
        user_id=contact.contact_id,
        login=contact.login,
        first_name=contact.first_name,
        last_name=contact.last_name,
    )


def _lead_summary(
    payload: DolibarrProjectPayload,
    owners: list[UserSummary],
) -> LeadSummary:
    return LeadSummary(
        project_id=payload.project_id,
        ref=payload.ref,
        title=payload.label,
        thirdparty_id=payload.thirdparty_id,
        stage_id=payload.stage_id,
        stage_code=payload.stage_code,
        project_state=_PROJECT_STATE_BY_API[payload.status],
        amount=payload.amount,
        probability_percent=payload.probability_percent,
        date_start=_timestamp_to_date(payload.date_start),
        date_end=_timestamp_to_date(payload.date_end),
        owners=owners,
    )


def _lead_detail(
    payload: DolibarrProjectPayload,
    owners: list[UserSummary],
) -> LeadDetail:
    summary = _lead_summary(payload, owners)
    return LeadDetail(
        **summary.model_dump(),
        description=_bounded_note(payload.description),
        public_note=_bounded_note(payload.public_note),
        private_note=_bounded_note(payload.private_note),
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


def _thirdparty_api_payload(
    data: ThirdpartyCreateInput | ThirdpartyUpdateInput,
) -> dict[str, object]:
    source = data.model_dump(exclude_none=True)
    payload: dict[str, object] = {}
    field_mapping = {
        "name": "name",
        "alias": "name_alias",
        "address": "address",
        "postal_code": "zip",
        "city": "town",
        "country_code": "country_code",
        "email": "email",
        "phone": "phone",
        "vat_number": "tva_intra",
        "public_note": "note_public",
        "private_note": "note_private",
    }
    for field, api_field in field_mapping.items():
        if field in source:
            payload[api_field] = source[field]
    if data.customer_status is not None:
        payload["client"] = _CUSTOMER_STATUS_TO_API[data.customer_status]
    if "country_code" in payload:
        payload["country_code"] = str(payload["country_code"]).upper()
    return payload


def _lead_api_payload(data: LeadCreateInput | LeadUpdateInput) -> dict[str, object]:
    source = data.model_dump(exclude_none=True)
    payload: dict[str, object] = {}
    field_mapping = {
        "thirdparty_id": "socid",
        "title": "title",
        "description": "description",
        "amount": "opp_amount",
        "probability_percent": "opp_percent",
        "public_note": "note_public",
        "private_note": "note_private",
    }
    for field, api_field in field_mapping.items():
        if field in source:
            payload[api_field] = source[field]
    for field in ("date_start", "date_end"):
        value = getattr(data, field)
        if value is not None:
            payload[field] = _date_to_timestamp(value)
    return payload


class SalesService:
    """Stateless sales facade over fixed Dolibarr REST operations."""

    def __init__(self, client: DolibarrClient) -> None:
        self._client = client

    async def thirdparty_search(
        self,
        api_key: str,
        *,
        query: str | None,
        customer_status: CustomerStatus | None,
        offset: int,
        limit: int,
    ) -> ThirdpartySearchResult:
        rows = await self._client.list_thirdparties(api_key)
        needle = _normalize_text(query)
        vat_needle = _normalize_vat(query)
        matches = []
        for row in rows:
            summary = _thirdparty_summary(row)
            if customer_status is not None and summary.customer_status != customer_status:
                continue
            haystack = " ".join(
                filter(
                    None,
                    (
                        _normalize_text(row.name),
                        _normalize_text(row.alias),
                        _normalize_text(row.email),
                        _normalize_vat(row.vat_number),
                    ),
                )
            )
            if needle and needle not in haystack and not (vat_needle and vat_needle in haystack):
                continue
            matches.append(summary)
        matches.sort(key=lambda row: (row.name.casefold(), row.thirdparty_id))
        selected = matches[offset : offset + limit]
        return ThirdpartySearchResult(
            total_count=len(matches),
            returned_count=len(selected),
            offset=offset,
            limit=limit,
            has_more=offset + len(selected) < len(matches),
            rows=selected,
        )

    async def thirdparty_get(self, api_key: str, thirdparty_id: int) -> ThirdpartyDetail:
        return _thirdparty_detail(await self._client.get_thirdparty(api_key, thirdparty_id))

    async def user_search(
        self,
        api_key: str,
        *,
        query: str | None,
        offset: int,
        limit: int,
    ) -> UserSearchResult:
        needle = _normalize_text(query)
        matches = []
        for user in await self._client.list_users(api_key):
            if user.status != 1:
                continue
            haystack = " ".join(
                filter(
                    None,
                    (
                        _normalize_text(user.login),
                        _normalize_text(user.first_name),
                        _normalize_text(user.last_name),
                    ),
                )
            )
            if needle and needle not in haystack:
                continue
            matches.append(_user_summary_from_payload(user))
        matches.sort(key=lambda row: ((row.login or "").casefold(), row.user_id))
        selected = matches[offset : offset + limit]
        return UserSearchResult(
            total_count=len(matches),
            returned_count=len(selected),
            offset=offset,
            limit=limit,
            has_more=offset + len(selected) < len(matches),
            rows=selected,
        )

    async def _leaders(
        self,
        api_key: str,
        project_id: int,
    ) -> tuple[list[DolibarrProjectContactPayload], list[UserSummary]]:
        contacts = await self._client.get_project_contacts(api_key, project_id)
        leaders = [contact for contact in contacts if _is_internal_leader(contact)]
        summaries = [_user_summary_from_contact(contact) for contact in leaders]
        summaries.sort(key=lambda row: row.user_id)
        return leaders, summaries

    async def _lead_payload(self, api_key: str, project_id: int) -> DolibarrProjectPayload:
        payload = await self._client.get_project(api_key, project_id)
        if not payload.usage_opportunity:
            raise DolibarrNotFoundError
        return payload

    async def lead_get(self, api_key: str, project_id: int) -> LeadDetail:
        payload = await self._lead_payload(api_key, project_id)
        _, owners = await self._leaders(api_key, project_id)
        return _lead_detail(payload, owners)

    async def lead_search(
        self,
        api_key: str,
        *,
        query: str | None,
        thirdparty_id: int | None,
        stage_id: int | None,
        project_state: ProjectState | None,
        owner_user_id: int | None,
        offset: int,
        limit: int,
    ) -> LeadSearchResult:
        needle = _normalize_text(query)
        candidates: list[DolibarrProjectPayload] = []
        owner_cache: dict[int, list[UserSummary]] = {}
        for project in await self._client.list_projects(api_key):
            if not project.usage_opportunity:
                continue
            if thirdparty_id is not None and project.thirdparty_id != thirdparty_id:
                continue
            if stage_id is not None and project.stage_id != stage_id:
                continue
            if project_state is not None and _PROJECT_STATE_BY_API[project.status] != project_state:
                continue
            haystack = " ".join(
                (
                    _normalize_text(project.ref),
                    _normalize_text(project.label),
                    _normalize_text(project.description),
                )
            )
            if needle and needle not in haystack:
                continue
            if owner_user_id is not None:
                _, owners = await self._leaders(api_key, project.project_id)
                owner_cache[project.project_id] = owners
                if all(owner.user_id != owner_user_id for owner in owners):
                    continue
            candidates.append(project)
        candidates.sort(key=lambda row: (row.label.casefold(), row.project_id))
        selected = candidates[offset : offset + limit]
        rows: list[LeadSummary] = []
        for project in selected:
            project_owners = owner_cache.get(project.project_id)
            if project_owners is None:
                _, project_owners = await self._leaders(api_key, project.project_id)
            rows.append(_lead_summary(project, project_owners))
        return LeadSearchResult(
            total_count=len(candidates),
            returned_count=len(rows),
            offset=offset,
            limit=limit,
            has_more=offset + len(rows) < len(candidates),
            rows=rows,
        )

    async def thirdparty_create(
        self,
        api_key: str,
        data: ThirdpartyCreateInput,
        *,
        apply: bool,
        confirmation_token: str | None,
    ) -> MutationPreview | MutationResult:
        proposed = data.model_dump(mode="json", exclude_none=True)
        duplicates: list[int] = []
        warnings: list[str] = []
        for row in await self._client.list_thirdparties(api_key):
            reasons = []
            if _normalize_text(row.name) == _normalize_text(data.name):
                reasons.append("name")
            if data.email and _normalize_text(row.email) == _normalize_text(data.email):
                reasons.append("email")
            if data.vat_number and _normalize_vat(row.vat_number) == _normalize_vat(
                data.vat_number
            ):
                reasons.append("VAT")
            if reasons:
                duplicates.append(row.thirdparty_id)
                warnings.append(
                    f"Possible duplicate thirdparty {row.thirdparty_id}: "
                    f"{', '.join(reasons)} match."
                )
        preview = MutationPreview(
            operation="thirdparty_create",
            target_kind="thirdparty",
            changes=_create_changes(proposed),
            warnings=warnings,
            confirmation_token=_confirmation_token(
                operation="thirdparty_create",
                target_id=None,
                proposed=proposed,
                current=None,
                duplicate_ids=duplicates,
            ),
        )
        if not _confirmed(preview, apply=apply, token=confirmation_token):
            return preview
        thirdparty_id = await self._client.create_thirdparty(
            api_key,
            _thirdparty_api_payload(data),
        )
        detail = await self.thirdparty_get(api_key, thirdparty_id)
        return MutationResult(
            operation=preview.operation,
            outcome="applied",
            target_id=thirdparty_id,
            warnings=warnings,
            thirdparty=detail,
        )

    async def thirdparty_update(
        self,
        api_key: str,
        thirdparty_id: int,
        data: ThirdpartyUpdateInput,
        *,
        apply: bool,
        confirmation_token: str | None,
    ) -> MutationPreview | MutationResult:
        current = await self.thirdparty_get(api_key, thirdparty_id)
        proposed = data.model_dump(mode="json", exclude_none=True)
        current_state = current.model_dump(mode="json")
        changes = _changes(current_state, proposed)
        preview = MutationPreview(
            operation="thirdparty_update",
            target_kind="thirdparty",
            target_id=thirdparty_id,
            changes=changes,
            warnings=[] if changes else ["No effective field changes."],
            confirmation_token=_confirmation_token(
                operation="thirdparty_update",
                target_id=thirdparty_id,
                proposed=proposed,
                current=current_state,
            ),
        )
        if not _confirmed(preview, apply=apply, token=confirmation_token):
            return preview
        if not changes:
            return MutationResult(
                operation=preview.operation,
                outcome="no_op",
                target_id=thirdparty_id,
                warnings=preview.warnings,
                thirdparty=current,
            )
        await self._client.update_thirdparty(
            api_key,
            thirdparty_id,
            _thirdparty_api_payload(data),
        )
        detail = await self.thirdparty_get(api_key, thirdparty_id)
        return MutationResult(
            operation=preview.operation,
            outcome="applied",
            target_id=thirdparty_id,
            thirdparty=detail,
        )

    async def lead_create(
        self,
        api_key: str,
        data: LeadCreateInput,
        *,
        apply: bool,
        confirmation_token: str | None,
    ) -> MutationPreview | MutationResult:
        thirdparty = await self._client.get_thirdparty(api_key, data.thirdparty_id)
        proposed = data.model_dump(mode="json", exclude_none=True)
        duplicates = [
            project.project_id
            for project in await self._client.list_projects(api_key)
            if project.usage_opportunity
            and project.thirdparty_id == data.thirdparty_id
            and _normalize_text(project.label) == _normalize_text(data.title)
        ]
        warnings = [f"Possible duplicate lead project {project_id}." for project_id in duplicates]
        preview = MutationPreview(
            operation="lead_create",
            target_kind="lead",
            changes=_create_changes(proposed),
            warnings=warnings,
            confirmation_token=_confirmation_token(
                operation="lead_create",
                target_id=None,
                proposed=proposed,
                current={"thirdparty": thirdparty.model_dump(mode="json")},
                duplicate_ids=duplicates,
            ),
        )
        if not _confirmed(preview, apply=apply, token=confirmation_token):
            return preview
        payload = _lead_api_payload(data)
        payload.update(
            {
                "ref": "auto",
                "usage_opportunity": 1,
                "fk_opp_status": data.stage_id,
                "status": 0,
            }
        )
        project_id = await self._client.create_project(api_key, payload)
        detail = await self.lead_get(api_key, project_id)
        return MutationResult(
            operation=preview.operation,
            outcome="applied",
            target_id=project_id,
            warnings=warnings,
            lead=detail,
        )

    async def lead_update(
        self,
        api_key: str,
        project_id: int,
        data: LeadUpdateInput,
        *,
        apply: bool,
        confirmation_token: str | None,
    ) -> MutationPreview | MutationResult:
        current = await self.lead_get(api_key, project_id)
        if data.thirdparty_id is not None and data.thirdparty_id != current.thirdparty_id:
            await self._client.get_thirdparty(api_key, data.thirdparty_id)
        proposed = data.model_dump(mode="json", exclude_none=True)
        current_state = current.model_dump(mode="json")
        changes = _changes(current_state, proposed)
        preview = MutationPreview(
            operation="lead_update",
            target_kind="lead",
            target_id=project_id,
            changes=changes,
            warnings=[] if changes else ["No effective field changes."],
            confirmation_token=_confirmation_token(
                operation="lead_update",
                target_id=project_id,
                proposed=proposed,
                current=current_state,
            ),
        )
        if not _confirmed(preview, apply=apply, token=confirmation_token):
            return preview
        if not changes:
            return MutationResult(
                operation=preview.operation,
                outcome="no_op",
                target_id=project_id,
                warnings=preview.warnings,
                lead=current,
            )
        await self._client.update_project(api_key, project_id, _lead_api_payload(data))
        detail = await self.lead_get(api_key, project_id)
        return MutationResult(
            operation=preview.operation,
            outcome="applied",
            target_id=project_id,
            lead=detail,
        )

    async def lead_change_status(
        self,
        api_key: str,
        project_id: int,
        stage_id: int,
        *,
        apply: bool,
        confirmation_token: str | None,
    ) -> MutationPreview | MutationResult:
        current = await self.lead_get(api_key, project_id)
        proposed: dict[str, object] = {"stage_id": stage_id}
        current_state = current.model_dump(mode="json")
        changes = _changes(current_state, proposed)
        preview = MutationPreview(
            operation="lead_change_status",
            target_kind="lead",
            target_id=project_id,
            changes=changes,
            warnings=[] if changes else ["Lead already has this sales stage."],
            confirmation_token=_confirmation_token(
                operation="lead_change_status",
                target_id=project_id,
                proposed=proposed,
                current=current_state,
            ),
        )
        if not _confirmed(preview, apply=apply, token=confirmation_token):
            return preview
        if not changes:
            return MutationResult(
                operation=preview.operation,
                outcome="no_op",
                target_id=project_id,
                warnings=preview.warnings,
                lead=current,
            )
        await self._client.update_project(api_key, project_id, {"fk_opp_status": stage_id})
        detail = await self.lead_get(api_key, project_id)
        return MutationResult(
            operation=preview.operation,
            outcome="applied",
            target_id=project_id,
            lead=detail,
        )

    async def lead_open_project(
        self,
        api_key: str,
        project_id: int,
        *,
        apply: bool,
        confirmation_token: str | None,
    ) -> MutationPreview | MutationResult:
        current = await self.lead_get(api_key, project_id)
        proposed: dict[str, object] = {"project_state": "open"}
        current_state = current.model_dump(mode="json")
        changes = _changes(current_state, proposed)
        preview = MutationPreview(
            operation="lead_open_project",
            target_kind="lead",
            target_id=project_id,
            changes=changes,
            warnings=[] if changes else ["Project is already open."],
            confirmation_token=_confirmation_token(
                operation="lead_open_project",
                target_id=project_id,
                proposed=proposed,
                current=current_state,
            ),
        )
        if not _confirmed(preview, apply=apply, token=confirmation_token):
            return preview
        if not changes:
            return MutationResult(
                operation=preview.operation,
                outcome="no_op",
                target_id=project_id,
                warnings=preview.warnings,
                lead=current,
            )
        await self._client.validate_project(api_key, project_id)
        detail = await self.lead_get(api_key, project_id)
        if detail.project_state != "open":
            return MutationResult(
                operation=preview.operation,
                outcome="partial",
                target_id=project_id,
                partial_errors=["Dolibarr did not return the project in open state."],
                lead=detail,
            )
        return MutationResult(
            operation=preview.operation,
            outcome="applied",
            target_id=project_id,
            lead=detail,
        )

    async def lead_assign(
        self,
        api_key: str,
        project_id: int,
        user_id: int,
        *,
        apply: bool,
        confirmation_token: str | None,
    ) -> MutationPreview | MutationResult:
        user = await self._client.get_user(api_key, user_id)
        if user.status != 1:
            raise DolibarrNotFoundError
        current = await self.lead_get(api_key, project_id)
        leader_relations, owners = await self._leaders(api_key, project_id)
        proposed: dict[str, object] = {"owner_user_id": user_id}
        current_state = current.model_dump(mode="json")
        current_state["leader_relation_ids"] = [leader.contact_id for leader in leader_relations]
        no_change = len(owners) == 1 and owners[0].user_id == user_id
        changes = (
            []
            if no_change
            else [
                MutationChange(
                    field="owner_user_ids",
                    before=",".join(str(owner.user_id) for owner in owners),
                    after=str(user_id),
                )
            ]
        )
        preview = MutationPreview(
            operation="lead_assign",
            target_kind="lead",
            target_id=project_id,
            changes=changes,
            warnings=[] if changes else ["User is already the sole PROJECTLEADER."],
            confirmation_token=_confirmation_token(
                operation="lead_assign",
                target_id=project_id,
                proposed=proposed,
                current=current_state,
            ),
        )
        if not _confirmed(preview, apply=apply, token=confirmation_token):
            return preview
        if no_change:
            return MutationResult(
                operation=preview.operation,
                outcome="no_op",
                target_id=project_id,
                warnings=preview.warnings,
                lead=current,
            )

        partial_errors: list[str] = []
        desired_present = any(owner.user_id == user_id for owner in owners)
        if not desired_present:
            try:
                await self._client.add_project_leader(api_key, project_id, user_id)
                desired_present = True
            except DolibarrError:
                partial_errors.append("Could not add the requested PROJECTLEADER.")

        if desired_present:
            for leader in leader_relations:
                if leader.contact_id == user_id:
                    continue
                try:
                    await self._client.delete_project_leader(
                        api_key,
                        project_id,
                        leader.contact_id,
                    )
                except DolibarrError:
                    partial_errors.append(
                        f"Could not remove previous PROJECTLEADER {leader.contact_id}."
                    )

        detail = await self.lead_get(api_key, project_id)
        outcome = "partial" if partial_errors else "applied"
        return MutationResult(
            operation=preview.operation,
            outcome=outcome,
            target_id=project_id,
            partial_errors=partial_errors,
            lead=detail,
        )
