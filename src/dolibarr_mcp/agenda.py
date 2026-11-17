"""Caller-authorized project history and one-shot, preview-confirmed agenda mutations."""

from __future__ import annotations

import hmac
from datetime import UTC, datetime
from typing import TYPE_CHECKING, cast

from dolibarr_mcp.agenda_dates import agenda_timezone, event_dates, search_dates
from dolibarr_mcp.agenda_models import (
    AgendaCompletion,
    AgendaEventCreateInput,
    AgendaEventDetail,
    AgendaEventSearchResult,
    AgendaEventSummary,
    AgendaMutationResponse,
    DolibarrAgendaEventPayload,
)
from dolibarr_mcp.errors import (
    AgendaConfirmationError,
    AgendaRequestError,
    DolibarrError,
    DolibarrNotFoundError,
    DolibarrPermissionDeniedError,
    DolibarrUnavailableError,
    InvalidDolibarrResponseError,
)
from dolibarr_mcp.projects import ProjectService
from dolibarr_mcp.sales import _bounded_note, _confirmation_token, _create_changes

if TYPE_CHECKING:
    from collections.abc import Callable
    from datetime import date

    from dolibarr_mcp.client import DolibarrClient
    from dolibarr_mcp.models import DolibarrProjectPayload, VerifiedIdentity

_TYPE_CODES = {"email": "AC_EMAIL", "phone_call": "AC_TEL", "meeting": "AC_RDV", "other": "AC_OTH"}
_PERCENTAGES: dict[AgendaCompletion, int] = {
    "not_started": 0,
    "in_progress": 50,
    "done": 100,
    "not_applicable": -1,
}
_UNCERTAIN = (DolibarrUnavailableError, InvalidDolibarrResponseError)
_NO_MAIL = "This records history only; the MCP tool sends no email or invitations."


def _summary(row: DolibarrAgendaEventPayload, timezone: str) -> AgendaEventSummary:
    zone = agenda_timezone(timezone)
    return AgendaEventSummary(
        event_id=row.event_id,
        title=row.title,
        type_code=row.type_code,
        type_label=row.type_label,
        event_code=row.event_code,
        automatic=row.type_kind == "systemauto" or row.type_code == "AC_OTH_AUTO",
        date_start=(
            datetime.fromtimestamp(row.date_start, UTC).astimezone(zone)
            if row.date_start is not None
            else None
        ),
        date_end=(
            datetime.fromtimestamp(row.date_end, UTC).astimezone(zone)
            if row.date_end is not None
            else None
        ),
        timezone=timezone,
        all_day=row.all_day,
        percentage=row.percentage,
        owner_user_id=row.owner_user_id,
        project_id=row.project_id,
        thirdparty_id=row.thirdparty_id,
    )


def _detail(row: DolibarrAgendaEventPayload, timezone: str) -> AgendaEventDetail:
    return AgendaEventDetail(
        **_summary(row, timezone).model_dump(),
        description=_bounded_note(row.description),
        public_note=_bounded_note(row.public_note),
        location=row.location,
    )


def _zone(name: str) -> None:
    try:
        agenda_timezone(name)
    except ValueError:
        raise AgendaRequestError from None


def _confirm(expected: str, supplied: str | None) -> None:
    if supplied is None or not hmac.compare_digest(expected, supplied):
        raise AgendaConfirmationError


class AgendaService:
    """All state/fact caches are local to one invocation; no identity or key is retained."""

    def __init__(self, client: DolibarrClient) -> None:
        self._client = client
        self._projects = ProjectService(client)

    async def _links(
        self,
        api_key: str,
        row: DolibarrAgendaEventPayload,
        projects: set[int],
        thirdparties: set[int],
    ) -> None:
        if row.project_id is not None and row.project_id not in projects:
            await self._projects.payload(api_key, row.project_id)
            projects.add(row.project_id)
        if row.thirdparty_id is not None and row.thirdparty_id not in thirdparties:
            partner = await self._client.get_thirdparty(api_key, row.thirdparty_id)
            if partner.thirdparty_id != row.thirdparty_id:
                raise InvalidDolibarrResponseError
            thirdparties.add(row.thirdparty_id)

    async def _readable(
        self,
        api_key: str,
        matches: Callable[[DolibarrAgendaEventPayload], bool],
        *,
        project_id: int | None = None,
        thirdparty_id: int | None = None,
    ) -> list[DolibarrAgendaEventPayload]:
        projects: set[int] = set()
        thirdparties: set[int] = set()
        rows: list[DolibarrAgendaEventPayload] = []
        for candidate in await self._client.list_agenda_events(
            api_key, project_id=project_id, thirdparty_id=thirdparty_id
        ):
            if not matches(candidate):
                continue
            try:
                row = await self._client.get_agenda_event(api_key, candidate.event_id)
                await self._links(api_key, row, projects, thirdparties)
            except (DolibarrNotFoundError, DolibarrPermissionDeniedError):
                continue
            if matches(row):
                rows.append(row)
        return rows

    async def get(self, api_key: str, event_id: int, *, timezone: str) -> AgendaEventDetail:
        """Check individual event and linked-object access before projecting any fields."""
        _zone(timezone)
        row = await self._client.get_agenda_event(api_key, event_id)
        await self._links(api_key, row, set(), set())
        return _detail(row, timezone)

    async def search(
        self,
        api_key: str,
        *,
        project_id: int | None,
        thirdparty_id: int | None,
        date_from: date | None,
        date_to: date | None,
        timezone: str,
        owner_user_id: int | None,
        type_code: str | None,
        status: AgendaCompletion | None,
        offset: int,
        limit: int,
    ) -> AgendaEventSearchResult:
        """Include manual and automatic history; date filters mean interval overlap."""
        try:
            lower, upper = search_dates(date_from, date_to, timezone)
        except ValueError:
            raise AgendaRequestError from None
        if project_id is not None:
            await self._projects.payload(api_key, project_id)
        if thirdparty_id is not None:
            await self._client.get_thirdparty(api_key, thirdparty_id)

        def matches(row: DolibarrAgendaEventPayload) -> bool:  # noqa: PLR0911 - fixed typed filters
            if project_id is not None and row.project_id != project_id:
                return False
            if thirdparty_id is not None and row.thirdparty_id != thirdparty_id:
                return False
            if owner_user_id is not None and row.owner_user_id != owner_user_id:
                return False
            if type_code is not None and row.type_code != type_code:
                return False
            if status == "in_progress":
                if not 0 < row.percentage < _PERCENTAGES["done"]:
                    return False
            elif status is not None and row.percentage != _PERCENTAGES[status]:
                return False
            ends_at = row.date_end if row.date_end is not None else row.date_start
            if lower is not None and (ends_at is None or ends_at < lower):
                return False
            return upper is None or (row.date_start is not None and row.date_start <= upper)

        candidates = await self._readable(
            api_key, matches, project_id=project_id, thirdparty_id=thirdparty_id
        )
        rows = [_summary(row, timezone) for row in candidates[offset : offset + limit]]
        return AgendaEventSearchResult(
            total_count=len(candidates),
            returned_count=len(rows),
            offset=offset,
            limit=limit,
            has_more=offset + len(rows) < len(candidates),
            rows=rows,
        )

    async def _create_context(
        self, api_key: str, data: AgendaEventCreateInput
    ) -> tuple[DolibarrProjectPayload, dict[str, object] | None]:
        project = await self._projects.payload(api_key, data.project_id)
        if data.thirdparty_id is not None and data.thirdparty_id != project.thirdparty_id:
            raise AgendaRequestError
        partner_state: dict[str, object] | None = None
        if project.thirdparty_id is not None:
            partner = await self._client.get_thirdparty(api_key, project.thirdparty_id)
            if partner.thirdparty_id != project.thirdparty_id:
                raise InvalidDolibarrResponseError
            partner_state = partner.model_dump(mode="json")
        return project, partner_state

    async def _duplicates(
        self, api_key: str, payload: dict[str, object]
    ) -> list[DolibarrAgendaEventPayload]:
        def candidate(row: DolibarrAgendaEventPayload) -> bool:
            return (
                row.project_id == payload["fk_project"]
                and row.owner_user_id == payload["userownerid"]
                and row.date_start == payload["datep"]
                and row.title == payload["label"]
                and row.type_code == payload["type_code"]
            )

        rows = await self._readable(
            api_key, candidate, project_id=cast("int", payload["fk_project"])
        )
        return [row for row in rows if _matches(row, payload)]

    async def create(
        self,
        api_key: str,
        identity: VerifiedIdentity,
        data: AgendaEventCreateInput,
        *,
        apply: bool,
        confirmation_token: str | None,
    ) -> AgendaMutationResponse:
        """Prepare or confirm one manual event; never automatically repeat a POST."""
        project, partner_state = await self._create_context(api_key, data)
        start, end, all_day = event_dates(data.start, data.end, data.timezone, data.all_day)
        payload: dict[str, object] = {
            "label": data.title,
            "type_code": _TYPE_CODES[data.event_type],
            "fk_project": project.project_id,
            "socid": project.thirdparty_id or 0,
            "userownerid": identity.user_id,
            "datep": int(start.timestamp()),
            "datef": int(end.timestamp()),
            "fulldayevent": int(all_day),
            "percentage": _PERCENTAGES[data.status],
            "note_private": data.description or "",
        }
        before = await self._duplicates(api_key, payload)
        proposed: dict[str, object] = {
            **payload,
            "timezone": data.timezone,
            "date_start": start.isoformat(),
            "date_end": end.isoformat(),
            "project_ref": project.ref,
            "project_title": project.label,
        }
        token = _confirmation_token(
            operation="agenda_event_create",
            target_id=None,
            proposed=proposed,
            current={"project": project.model_dump(mode="json"), "thirdparty": partner_state},
            duplicate_ids=[row.event_id for row in before],
        )
        warnings = [_NO_MAIL]
        if before:
            warnings.append(
                "Matching events already exist; inspect matching_event_ids before creating."
            )
        if not apply:
            return AgendaMutationResponse(
                operation="agenda_event_create",
                phase="preview",
                warnings=warnings,
                confirmation_token=token,
                changes=_create_changes(proposed),
                matching_event_ids=[row.event_id for row in before],
            )
        _confirm(token, confirmation_token)
        event_id: int | None = None
        errors: list[str] = []
        try:
            event_id = await self._client.create_agenda_event(api_key, payload)
        except _UNCERTAIN:
            errors.append(
                "Event creation outcome is uncertain; inspect project history before retrying."
            )
        return await self._created_result(
            api_key,
            event_id,
            payload,
            data.timezone,
            before=before,
            warnings=warnings,
            errors=errors,
        )

    async def _created_result(
        self,
        api_key: str,
        event_id: int | None,
        payload: dict[str, object],
        timezone: str,
        *,
        before: list[DolibarrAgendaEventPayload],
        warnings: list[str],
        errors: list[str],
    ) -> AgendaMutationResponse:
        detail: AgendaEventDetail | None = None
        matches: list[int] = []
        try:
            if event_id is not None:
                row = await self._client.get_agenda_event(api_key, event_id)
                await self._links(api_key, row, set(), set())
                if _matches(row, payload):
                    detail, matches = _detail(row, timezone), [row.event_id]
                else:
                    errors.append("The returned event does not match the confirmed proposal.")
            else:
                old_ids = {row.event_id for row in before}
                new = [
                    row
                    for row in await self._duplicates(api_key, payload)
                    if row.event_id not in old_ids
                ]
                matches = [row.event_id for row in new]
                if len(new) == 1:
                    event_id, detail = new[0].event_id, _detail(new[0], timezone)
        except DolibarrError:
            errors.append("Could not verify event creation; search history before retrying.")
        return AgendaMutationResponse(
            operation="agenda_event_create",
            phase="result",
            apply=True,
            target_id=event_id,
            outcome="partial" if errors else "applied",
            warnings=warnings,
            partial_errors=errors,
            agenda_event=detail,
            matching_event_ids=matches,
        )

    async def delete(
        self,
        api_key: str,
        identity: VerifiedIdentity,
        event_id: int,
        *,
        timezone: str,
        apply: bool,
        confirmation_token: str | None,
    ) -> AgendaMutationResponse:
        """Compare the current allowlisted snapshot, then delete exactly one ID once."""
        _zone(timezone)
        row = await self._client.get_agenda_event(api_key, event_id)
        await self._links(api_key, row, set(), set())
        shown = _detail(row, timezone)
        current: dict[str, object] = {
            "event": row.model_dump(mode="json"),
            "verified_user_id": identity.user_id,
        }
        token = _confirmation_token(
            operation="agenda_event_delete",
            target_id=event_id,
            proposed={"delete": event_id, "timezone": timezone},
            current=current,
        )
        warnings = ["This permanently deletes exactly one agenda/history event."]
        if not apply:
            return AgendaMutationResponse(
                operation="agenda_event_delete",
                phase="preview",
                target_id=event_id,
                confirmation_token=token,
                warnings=warnings,
                agenda_event=shown,
                changes=_create_changes(
                    {
                        "event_id": event_id,
                        "title": shown.title,
                        "date_start": shown.date_start.isoformat() if shown.date_start else None,
                        "date_end": shown.date_end.isoformat() if shown.date_end else None,
                        "owner_user_id": shown.owner_user_id,
                        "project_id": shown.project_id,
                    }
                ),
            )
        _confirm(token, confirmation_token)
        errors: list[str] = []
        try:
            await self._client.delete_agenda_event(api_key, event_id)
        except _UNCERTAIN:
            errors.append("Deletion outcome is uncertain; inspect this event ID before retrying.")
        try:
            await self._client.get_agenda_event(api_key, event_id)
        except DolibarrNotFoundError:
            pass
        except DolibarrError:
            errors.append("Could not verify deletion; access failure is not proof of absence.")
        else:
            errors.append("The event still exists after the deletion attempt.")
        return AgendaMutationResponse(
            operation="agenda_event_delete",
            phase="result",
            apply=True,
            target_id=event_id,
            outcome="partial" if errors else "applied",
            warnings=warnings,
            partial_errors=errors,
        )


def _matches(row: DolibarrAgendaEventPayload, payload: dict[str, object]) -> bool:
    """Verify the complete submitted proposal, not just a returned positive identifier."""
    return (
        row.title == payload["label"]
        and row.type_code == payload["type_code"]
        and row.project_id == payload["fk_project"]
        and (row.thirdparty_id or 0) == payload["socid"]
        and row.owner_user_id == payload["userownerid"]
        and row.date_start == payload["datep"]
        and row.date_end == payload["datef"]
        and row.all_day == bool(payload["fulldayevent"])
        and row.percentage == payload["percentage"]
        and (row.description or "") == payload["note_private"]
    )
