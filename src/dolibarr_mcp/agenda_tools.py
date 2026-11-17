"""Register project and agenda MCP schemas without widening existing lead tools."""

from __future__ import annotations

from datetime import date  # noqa: TC003 - MCP resolves runtime schemas
from functools import wraps
from typing import TYPE_CHECKING, Annotated

from mcp.server.mcpserver.exceptions import ToolError
from mcp.types import ToolAnnotations
from pydantic import Field, ValidationError

from dolibarr_mcp.agenda import AgendaService
from dolibarr_mcp.agenda_models import (
    AgendaCompletion,
    AgendaEventCreateInput,
    AgendaEventDetail,
    AgendaEventSearchResult,
    AgendaEventType,
    AgendaMutationResponse,
    ProjectReadDetail,
    ProjectSearchResult,
)
from dolibarr_mcp.credentials import get_request_api_key
from dolibarr_mcp.errors import DolibarrError
from dolibarr_mcp.models import ProjectState  # noqa: TC001 - MCP resolves runtime schemas
from dolibarr_mcp.projects import ProjectService
from dolibarr_mcp.reporting import MAX_OUTPUT_ROWS

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from mcp.server import MCPServer

    from dolibarr_mcp.client import DolibarrClient
    from dolibarr_mcp.models import VerifiedIdentity


def _domain_errors[**P, T](function: Callable[P, Awaitable[T]]) -> Callable[P, Awaitable[T]]:
    """Return only deliberate public messages, never validation inputs or upstream details."""

    @wraps(function)
    async def wrapped(*args: P.args, **kwargs: P.kwargs) -> T:
        try:
            return await function(*args, **kwargs)
        except DolibarrError as error:
            raise ToolError(error.public_message) from None
        except ValidationError:
            message = "Invalid agenda event parameters."
            raise ToolError(message) from None

    return wrapped


PositiveID = Annotated[int, Field(gt=0)]
PageLimit = Annotated[int, Field(ge=1, le=MAX_OUTPUT_ROWS)]
PageOffset = Annotated[int, Field(ge=0, le=1_000_000)]
Timezone = Annotated[str, Field(min_length=1, max_length=128)]
Token = Annotated[str, Field(min_length=64, max_length=64)]
DateText = Annotated[str, Field(min_length=10, max_length=64)]
TypeCode = Annotated[str, Field(min_length=1, max_length=64, pattern=r"^[A-Za-z0-9_]+$")]
_READ = ToolAnnotations(
    readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=False
)
_CREATE = ToolAnnotations(
    readOnlyHint=False, destructiveHint=False, idempotentHint=False, openWorldHint=False
)
_DELETE = ToolAnnotations(
    readOnlyHint=False, destructiveHint=True, idempotentHint=False, openWorldHint=False
)


def register_project_agenda_tools(
    server: MCPServer[None], client: DolibarrClient, identity: Callable[[], VerifiedIdentity]
) -> MCPServer[None]:
    """Append the six tools using the existing request credential and verified identity."""
    projects, agenda = ProjectService(client), AgendaService(client)

    @server.tool(
        name="dolibarr_project_search",
        description=(
            "Search all readable projects by ref/title substring, thirdparty_id, project_state, "
            "or optional usage_opportunity. No open-project or lead-only default. "
            "Ascending project ID; offset/limit/has_more. Scans at most 10,000 projects, "
            "then fails explicitly instead of silently truncating."
        ),
        annotations=_READ,
    )
    @_domain_errors
    async def dolibarr_project_search(
        *,
        query: Annotated[str, Field(min_length=1, max_length=255)] | None = None,
        thirdparty_id: PositiveID | None = None,
        project_state: ProjectState | None = None,
        usage_opportunity: bool | None = None,
        offset: PageOffset = 0,
        limit: PageLimit = 200,
    ) -> ProjectSearchResult:
        return await projects.search(
            get_request_api_key(),
            query=query,
            thirdparty_id=thirdparty_id,
            project_state=project_state,
            usage_opportunity=usage_opportunity,
            offset=offset,
            limit=limit,
        )

    @server.tool(
        name="dolibarr_project_get",
        description=(
            "Read safe details of one project regardless of opportunity flag or lifecycle state. "
            "Project public/private notes are fields, distinct from agenda/history events."
        ),
        annotations=_READ,
    )
    @_domain_errors
    async def dolibarr_project_get(project_id: PositiveID) -> ProjectReadDetail:
        return await projects.get(get_request_api_key(), project_id)

    @server.tool(
        name="dolibarr_agenda_event_search",
        description=(
            "Read agenda/project history, including automatic entries such as project changes. "
            "Filter by project_id, thirdparty_id, local date overlap, owner_user_id, type_code "
            "(e.g. AC_EMAIL), or status. in_progress means 1..99 percent; done means 100. "
            "Dates are inclusive in the IANA timezone (default UTC). Ascending event ID with "
            "offset/limit/has_more; explicit error at the 10,000-event scan bound."
        ),
        annotations=_READ,
    )
    @_domain_errors
    async def dolibarr_agenda_event_search(
        *,
        project_id: PositiveID | None = None,
        thirdparty_id: PositiveID | None = None,
        date_from: date | None = None,
        date_to: date | None = None,
        timezone: Timezone = "UTC",
        owner_user_id: PositiveID | None = None,
        type_code: TypeCode | None = None,
        status: AgendaCompletion | None = None,
        offset: PageOffset = 0,
        limit: PageLimit = 200,
    ) -> AgendaEventSearchResult:
        return await agenda.search(
            get_request_api_key(),
            project_id=project_id,
            thirdparty_id=thirdparty_id,
            date_from=date_from,
            date_to=date_to,
            timezone=timezone,
            owner_user_id=owner_user_id,
            type_code=type_code,
            status=status,
            offset=offset,
            limit=limit,
        )

    @server.tool(
        name="dolibarr_agenda_event_get",
        description=(
            "Read allowlisted details of one agenda/history event in an IANA timezone "
            "(default UTC). No raw objects, mail headers, recipients, or extrafields."
        ),
        annotations=_READ,
    )
    @_domain_errors
    async def dolibarr_agenda_event_get(
        event_id: PositiveID, *, timezone: Timezone = "UTC"
    ) -> AgendaEventDetail:
        return await agenda.get(get_request_api_key(), event_id, timezone=timezone)

    @server.tool(
        name="dolibarr_agenda_event_create",
        description=(
            "Preview (apply=false), then repeat the same arguments with confirmation_token "
            "and apply=true to record a manual project history event. This is not a project-note "
            "edit and sends no email or invitations. event_type=email maps to AC_EMAIL; "
            "status=done means percentage=100. Owner is the authenticated user; third party "
            "comes from the project. Use start/end as YYYY-MM-DD, DD.MM.YYYY, or ISO date-times "
            "and an IANA timezone. Date-only input implies all_day=true; end dates are inclusive. "
            "Ambiguous DST times need an explicit UTC offset. Never retry an uncertain POST "
            "without searching history and inspecting matching_event_ids."
        ),
        annotations=_CREATE,
    )
    @_domain_errors
    async def dolibarr_agenda_event_create(
        title: Annotated[str, Field(min_length=1, max_length=128)],
        event_type: AgendaEventType,
        project_id: PositiveID,
        start: DateText,
        *,
        end: DateText | None = None,
        timezone: Timezone = "UTC",
        all_day: bool | None = None,
        status: AgendaCompletion = "not_started",
        description: Annotated[str, Field(max_length=4000)] | None = None,
        thirdparty_id: PositiveID | None = None,
        apply: bool = False,
        confirmation_token: Token | None = None,
    ) -> AgendaMutationResponse:
        data = AgendaEventCreateInput(
            title=title,
            event_type=event_type,
            project_id=project_id,
            start=start,
            end=end,
            timezone=timezone,
            all_day=all_day,
            status=status,
            description=description,
            thirdparty_id=thirdparty_id,
        )
        return await agenda.create(
            get_request_api_key(),
            identity(),
            data,
            apply=apply,
            confirmation_token=confirmation_token,
        )

    @server.tool(
        name="dolibarr_agenda_event_delete",
        description=(
            "Preview deletion of exactly one agenda/history event, showing title, dates, owner "
            "and project. Repeat with apply=true and confirmation_token. Changed event state "
            "invalidates the preview. No bulk delete, no automatic retry; absence is verified "
            "by rereading the same ID. Concurrent upstream edits cannot be locked by this API."
        ),
        annotations=_DELETE,
    )
    @_domain_errors
    async def dolibarr_agenda_event_delete(
        event_id: PositiveID,
        *,
        timezone: Timezone = "UTC",
        apply: bool = False,
        confirmation_token: Token | None = None,
    ) -> AgendaMutationResponse:
        return await agenda.delete(
            get_request_api_key(),
            identity(),
            event_id,
            timezone=timezone,
            apply=apply,
            confirmation_token=confirmation_token,
        )

    return server
