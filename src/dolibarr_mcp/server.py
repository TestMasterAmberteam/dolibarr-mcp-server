"""MCP server definition containing the allowlisted read-only tools."""

from __future__ import annotations

from datetime import date  # noqa: TC003 - MCP resolves tool schemas at runtime
from typing import TYPE_CHECKING, Annotated

from mcp.server import MCPServer
from mcp.server.auth.middleware.auth_context import get_access_token
from mcp.types import ToolAnnotations
from pydantic import Field, ValidationError

from dolibarr_mcp.credentials import get_request_api_key
from dolibarr_mcp.models import (
    MyTimeReport,
    ProjectTimeReport,
    SummaryGroup,
    TaskTimespentReport,
    TimeEntriesReport,
    TimeSummary,
    VerifiedIdentity,
)
from dolibarr_mcp.reporting import MAX_OUTPUT_ROWS, TimeReportingService

if TYPE_CHECKING:
    from dolibarr_mcp.client import DolibarrClient

PositiveIdentifier = Annotated[int, Field(gt=0)]
OutputLimit = Annotated[int, Field(ge=1, le=MAX_OUTPUT_ROWS)]
ResultOffset = Annotated[int, Field(ge=0, le=1_000_000)]

_READ_ONLY_ANNOTATIONS = ToolAnnotations(
    readOnlyHint=True,
    destructiveHint=False,
    idempotentHint=True,
    openWorldHint=False,
)


def _current_identity() -> VerifiedIdentity:
    access_token = get_access_token()
    if access_token is None or access_token.claims is None:
        message = "Authentication context is unavailable"
        raise PermissionError(message)
    try:
        return VerifiedIdentity.model_validate(access_token.claims.get("identity"))
    except ValidationError:
        message = "Authentication context is invalid"
        raise PermissionError(message) from None


def create_mcp_server(client: DolibarrClient) -> MCPServer[None]:
    """Build the single shared MCP protocol server and stateless report service."""
    server: MCPServer[None] = MCPServer(
        name="dolibarr-mcp-server",
        description="Stateless, per-user access to Dolibarr ERP.",
        version="0.1.0",
    )
    reporting = TimeReportingService(client)

    @server.tool(
        name="dolibarr_whoami",
        description="Return the safe Dolibarr identity verified for this HTTP request.",
        annotations=_READ_ONLY_ANNOTATIONS,
    )
    async def dolibarr_whoami() -> VerifiedIdentity:
        """Return the already verified identity without another Dolibarr request."""
        return _current_identity()

    @server.tool(
        name="dolibarr_my_time_report",
        description=(
            "Return the authenticated user's time grouped by day, project, and task for an "
            "inclusive date range."
        ),
        annotations=_READ_ONLY_ANNOTATIONS,
    )
    async def dolibarr_my_time_report(
        date_from: date,
        date_to: date,
        limit: OutputLimit = 200,
    ) -> MyTimeReport:
        return await reporting.my_time_report(
            get_request_api_key(),
            _current_identity(),
            date_from=date_from,
            date_to=date_to,
            limit=limit,
        )

    @server.tool(
        name="dolibarr_project_time_report",
        description=(
            "Return authorized time in one project, grouped independently by user and task, "
            "for an inclusive date range."
        ),
        annotations=_READ_ONLY_ANNOTATIONS,
    )
    async def dolibarr_project_time_report(
        project_id: PositiveIdentifier,
        date_from: date,
        date_to: date,
        limit: OutputLimit = 200,
    ) -> ProjectTimeReport:
        return await reporting.project_time_report(
            get_request_api_key(),
            project_id=project_id,
            date_from=date_from,
            date_to=date_to,
            limit=limit,
        )

    @server.tool(
        name="dolibarr_task_timespent",
        description=(
            "Return paged detailed time entries for one authorized task and inclusive date range."
        ),
        annotations=_READ_ONLY_ANNOTATIONS,
    )
    async def dolibarr_task_timespent(
        task_id: PositiveIdentifier,
        date_from: date,
        date_to: date,
        offset: ResultOffset = 0,
        limit: OutputLimit = 200,
    ) -> TaskTimespentReport:
        return await reporting.task_timespent(
            get_request_api_key(),
            task_id=task_id,
            date_from=date_from,
            date_to=date_to,
            offset=offset,
            limit=limit,
        )

    @server.tool(
        name="dolibarr_time_summary",
        description=(
            "Aggregate authorized time by user, project, task, calendar month, or ISO week. "
            "Optional positive IDs apply fixed filters."
        ),
        annotations=_READ_ONLY_ANNOTATIONS,
    )
    async def dolibarr_time_summary(
        date_from: date,
        date_to: date,
        group_by: SummaryGroup,
        *,
        user_id: PositiveIdentifier | None = None,
        project_id: PositiveIdentifier | None = None,
        task_id: PositiveIdentifier | None = None,
        limit: OutputLimit = 200,
    ) -> TimeSummary:
        return await reporting.time_summary(
            get_request_api_key(),
            date_from=date_from,
            date_to=date_to,
            group_by=group_by,
            user_id=user_id,
            project_id=project_id,
            task_id=task_id,
            limit=limit,
        )

    @server.tool(
        name="dolibarr_time_entries",
        description=(
            "Return paged raw authorized time entries with dates, durations, users, projects, "
            "tasks, and bounded notes. Optional positive IDs apply fixed filters."
        ),
        annotations=_READ_ONLY_ANNOTATIONS,
    )
    async def dolibarr_time_entries(
        date_from: date,
        date_to: date,
        *,
        user_id: PositiveIdentifier | None = None,
        project_id: PositiveIdentifier | None = None,
        task_id: PositiveIdentifier | None = None,
        offset: ResultOffset = 0,
        limit: OutputLimit = 200,
    ) -> TimeEntriesReport:
        return await reporting.time_entries(
            get_request_api_key(),
            date_from=date_from,
            date_to=date_to,
            user_id=user_id,
            project_id=project_id,
            task_id=task_id,
            offset=offset,
            limit=limit,
        )

    return server
