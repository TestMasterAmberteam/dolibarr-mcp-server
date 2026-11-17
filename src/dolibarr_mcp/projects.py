"""Ordinary project and opportunity reads without changing legacy lead semantics."""

from __future__ import annotations

from typing import TYPE_CHECKING

from dolibarr_mcp.agenda_models import ProjectReadDetail, ProjectSearchResult, ProjectSummary
from dolibarr_mcp.errors import (
    DolibarrNotFoundError,
    DolibarrPermissionDeniedError,
    InvalidDolibarrResponseError,
)
from dolibarr_mcp.sales import (
    _PROJECT_STATE_BY_API,
    _bounded_note,
    _normalize_text,
    _timestamp_to_date,
)

if TYPE_CHECKING:
    from dolibarr_mcp.client import DolibarrClient
    from dolibarr_mcp.models import DolibarrProjectPayload, ProjectState


def project_summary(payload: DolibarrProjectPayload) -> ProjectSummary:
    """Project notes and opportunity pipeline metadata are not search fields."""
    return ProjectSummary(
        project_id=payload.project_id,
        ref=payload.ref,
        title=payload.label,
        thirdparty_id=payload.thirdparty_id,
        project_state=_PROJECT_STATE_BY_API[payload.status],
        usage_opportunity=payload.usage_opportunity,
        date_start=_timestamp_to_date(payload.date_start),
        date_end=_timestamp_to_date(payload.date_end),
    )


class ProjectService:
    """Per-request reads from the existing fixed project endpoints."""

    def __init__(self, client: DolibarrClient) -> None:
        self._client = client

    async def payload(self, api_key: str, project_id: int) -> DolibarrProjectPayload:
        """Require the requested object, rather than trusting a mismatched response ID."""
        row = await self._client.get_project(api_key, project_id)
        if row.project_id != project_id:
            raise InvalidDolibarrResponseError
        return row

    async def get(self, api_key: str, project_id: int) -> ProjectReadDetail:
        """Read any accessible project, including an opportunity or closed project."""
        row = await self.payload(api_key, project_id)
        return ProjectReadDetail(
            **project_summary(row).model_dump(),
            description=_bounded_note(row.description),
            public_note=_bounded_note(row.public_note),
            private_note=_bounded_note(row.private_note),
        )

    async def search(
        self,
        api_key: str,
        *,
        query: str | None,
        thirdparty_id: int | None,
        project_state: ProjectState | None,
        usage_opportunity: bool | None,
        offset: int,
        limit: int,
    ) -> ProjectSearchResult:
        """Filter locally, verify GET access, then page in ascending project-ID order."""
        needle = _normalize_text(query)

        def matches(row: DolibarrProjectPayload) -> bool:
            return (
                (thirdparty_id is None or row.thirdparty_id == thirdparty_id)
                and (project_state is None or _PROJECT_STATE_BY_API[row.status] == project_state)
                and (usage_opportunity is None or row.usage_opportunity == usage_opportunity)
                and (
                    not needle
                    or needle in _normalize_text(row.ref)
                    or needle in _normalize_text(row.label)
                )
            )

        readable: dict[int, ProjectSummary] = {}
        for row in await self._client.list_projects(api_key):
            if not matches(row):
                continue
            try:
                current = await self.payload(api_key, row.project_id)
            except (DolibarrNotFoundError, DolibarrPermissionDeniedError):
                continue
            if matches(current):
                readable[current.project_id] = project_summary(current)
        candidates = sorted(readable.values(), key=lambda row: row.project_id)
        rows = candidates[offset : offset + limit]
        return ProjectSearchResult(
            total_count=len(candidates),
            returned_count=len(rows),
            offset=offset,
            limit=limit,
            has_more=offset + len(rows) < len(candidates),
            rows=rows,
        )
