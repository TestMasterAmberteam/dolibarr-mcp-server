"""MCP server definition containing allowlisted reporting, sales, and leave tools."""

from __future__ import annotations

from datetime import date  # noqa: TC003 - MCP resolves tool schemas at runtime
from typing import TYPE_CHECKING, Annotated

from mcp.server import MCPServer
from mcp.server.auth.middleware.auth_context import get_access_token
from mcp.types import ToolAnnotations
from pydantic import Field, ValidationError

from dolibarr_mcp.credentials import get_request_api_key
from dolibarr_mcp.leave_requests import LeaveRequestService
from dolibarr_mcp.models import (
    CustomerStatus,
    LeadCreateInput,
    LeadDetail,
    LeadSearchResult,
    LeadUpdateInput,
    LeaveHalfDayMode,
    LeaveRequestCreateInput,
    LeaveRequestDetail,
    LeaveRequestSearchResult,
    LeaveRequestStatus,
    LeaveRequestUpdateInput,
    LeaveTypeListResult,
    MutationPreview,
    MutationResponse,
    MutationResult,
    MyTimeReport,
    ProjectState,
    ProjectTimeReport,
    SummaryGroup,
    TaskTimespentReport,
    ThirdpartyCreateInput,
    ThirdpartyDetail,
    ThirdpartySearchResult,
    ThirdpartyUpdateInput,
    TimeEntriesReport,
    TimeSummary,
    UserSearchResult,
    VerifiedIdentity,
)
from dolibarr_mcp.reporting import MAX_OUTPUT_ROWS, TimeReportingService
from dolibarr_mcp.sales import SalesService

if TYPE_CHECKING:
    from dolibarr_mcp.client import DolibarrClient

PositiveIdentifier = Annotated[int, Field(gt=0)]
OutputLimit = Annotated[int, Field(ge=1, le=MAX_OUTPUT_ROWS)]
ResultOffset = Annotated[int, Field(ge=0, le=1_000_000)]
QueryText = Annotated[str, Field(min_length=1, max_length=255)]
ShortText = Annotated[str, Field(min_length=1, max_length=255)]
OptionalText = Annotated[str, Field(max_length=1000)]
NoteText = Annotated[str, Field(max_length=4000)]
RequiredNote = Annotated[str, Field(min_length=1, max_length=4000)]
CountryCode = Annotated[str, Field(min_length=2, max_length=3)]
EmailText = Annotated[str, Field(max_length=320)]
PhoneText = Annotated[str, Field(max_length=64)]
VatText = Annotated[str, Field(max_length=64)]
Amount = Annotated[float, Field(ge=0)]
Probability = Annotated[float, Field(ge=0, le=100)]
ConfirmationToken = Annotated[str, Field(min_length=64, max_length=64)]

_READ_ONLY_ANNOTATIONS = ToolAnnotations(
    readOnlyHint=True,
    destructiveHint=False,
    idempotentHint=True,
    openWorldHint=False,
)

_CREATE_ANNOTATIONS = ToolAnnotations(
    readOnlyHint=False,
    destructiveHint=False,
    idempotentHint=False,
    openWorldHint=False,
)

_MUTATION_ANNOTATIONS = ToolAnnotations(
    readOnlyHint=False,
    destructiveHint=True,
    idempotentHint=False,
    openWorldHint=False,
)


def _defined(**values: object) -> dict[str, object]:
    return {key: value for key, value in values.items() if value is not None}


def _mutation_response(value: MutationPreview | MutationResult) -> MutationResponse:
    return MutationResponse.from_mutation(value)


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
    """Build the shared MCP protocol server and stateless domain services."""
    server: MCPServer[None] = MCPServer(
        name="dolibarr-mcp-server",
        description="Stateless, per-user access to Dolibarr ERP.",
        version="0.3.0",
    )
    reporting = TimeReportingService(client)
    sales = SalesService(client)
    leave_requests = LeaveRequestService(client)

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

    @server.tool(
        name="dolibarr_thirdparty_search",
        description=(
            "Search accessible Dolibarr third parties by safe fields and customer classification."
        ),
        annotations=_READ_ONLY_ANNOTATIONS,
    )
    async def dolibarr_thirdparty_search(
        *,
        query: QueryText | None = None,
        customer_status: CustomerStatus | None = None,
        offset: ResultOffset = 0,
        limit: OutputLimit = 200,
    ) -> ThirdpartySearchResult:
        return await sales.thirdparty_search(
            get_request_api_key(),
            query=query,
            customer_status=customer_status,
            offset=offset,
            limit=limit,
        )

    @server.tool(
        name="dolibarr_thirdparty_get",
        description="Return allowlisted details for one accessible Dolibarr third party.",
        annotations=_READ_ONLY_ANNOTATIONS,
    )
    async def dolibarr_thirdparty_get(
        thirdparty_id: PositiveIdentifier,
    ) -> ThirdpartyDetail:
        return await sales.thirdparty_get(get_request_api_key(), thirdparty_id)

    @server.tool(
        name="dolibarr_thirdparty_create",
        description=(
            "Preview or explicitly confirm creation of a third party. "
            "Call first with apply=false, then repeat with its confirmation_token and apply=true."
        ),
        annotations=_CREATE_ANNOTATIONS,
    )
    async def dolibarr_thirdparty_create(
        name: ShortText,
        customer_status: CustomerStatus,
        *,
        alias: ShortText | None = None,
        address: OptionalText | None = None,
        postal_code: Annotated[str, Field(max_length=32)] | None = None,
        city: ShortText | None = None,
        country_code: CountryCode | None = None,
        email: EmailText | None = None,
        phone: PhoneText | None = None,
        vat_number: VatText | None = None,
        public_note: NoteText | None = None,
        private_note: NoteText | None = None,
        apply: bool = False,
        confirmation_token: ConfirmationToken | None = None,
    ) -> MutationResponse:
        data = ThirdpartyCreateInput(
            name=name,
            customer_status=customer_status,
            alias=alias,
            address=address,
            postal_code=postal_code,
            city=city,
            country_code=country_code,
            email=email,
            phone=phone,
            vat_number=vat_number,
            public_note=public_note,
            private_note=private_note,
        )
        return _mutation_response(
            await sales.thirdparty_create(
                get_request_api_key(),
                data,
                apply=apply,
                confirmation_token=confirmation_token,
            )
        )

    @server.tool(
        name="dolibarr_thirdparty_update",
        description=(
            "Preview or explicitly confirm an allowlisted partial third-party update. "
            "Call first with apply=false, then repeat with its confirmation_token and apply=true."
        ),
        annotations=_MUTATION_ANNOTATIONS,
    )
    async def dolibarr_thirdparty_update(
        thirdparty_id: PositiveIdentifier,
        *,
        name: ShortText | None = None,
        customer_status: CustomerStatus | None = None,
        alias: Annotated[str, Field(max_length=255)] | None = None,
        address: OptionalText | None = None,
        postal_code: Annotated[str, Field(max_length=32)] | None = None,
        city: Annotated[str, Field(max_length=255)] | None = None,
        country_code: CountryCode | None = None,
        email: EmailText | None = None,
        phone: PhoneText | None = None,
        vat_number: VatText | None = None,
        public_note: NoteText | None = None,
        private_note: NoteText | None = None,
        apply: bool = False,
        confirmation_token: ConfirmationToken | None = None,
    ) -> MutationResponse:
        data = ThirdpartyUpdateInput.model_validate(
            _defined(
                name=name,
                customer_status=customer_status,
                alias=alias,
                address=address,
                postal_code=postal_code,
                city=city,
                country_code=country_code,
                email=email,
                phone=phone,
                vat_number=vat_number,
                public_note=public_note,
                private_note=private_note,
            )
        )
        return _mutation_response(
            await sales.thirdparty_update(
                get_request_api_key(),
                thirdparty_id,
                data,
                apply=apply,
                confirmation_token=confirmation_token,
            )
        )

    @server.tool(
        name="dolibarr_user_search",
        description="Search accessible active Dolibarr users for lead assignment.",
        annotations=_READ_ONLY_ANNOTATIONS,
    )
    async def dolibarr_user_search(
        *,
        query: QueryText | None = None,
        offset: ResultOffset = 0,
        limit: OutputLimit = 200,
    ) -> UserSearchResult:
        return await sales.user_search(
            get_request_api_key(),
            query=query,
            offset=offset,
            limit=limit,
        )

    @server.tool(
        name="dolibarr_lead_search",
        description=("Search accessible project leads, defined strictly by usage_opportunity=1."),
        annotations=_READ_ONLY_ANNOTATIONS,
    )
    async def dolibarr_lead_search(
        *,
        query: QueryText | None = None,
        thirdparty_id: PositiveIdentifier | None = None,
        stage_id: PositiveIdentifier | None = None,
        project_state: ProjectState | None = None,
        owner_user_id: PositiveIdentifier | None = None,
        offset: ResultOffset = 0,
        limit: OutputLimit = 200,
    ) -> LeadSearchResult:
        return await sales.lead_search(
            get_request_api_key(),
            query=query,
            thirdparty_id=thirdparty_id,
            stage_id=stage_id,
            project_state=project_state,
            owner_user_id=owner_user_id,
            offset=offset,
            limit=limit,
        )

    @server.tool(
        name="dolibarr_lead_get",
        description="Return allowlisted details for one project marked as a Dolibarr lead.",
        annotations=_READ_ONLY_ANNOTATIONS,
    )
    async def dolibarr_lead_get(project_id: PositiveIdentifier) -> LeadDetail:
        return await sales.lead_get(get_request_api_key(), project_id)

    @server.tool(
        name="dolibarr_lead_create",
        description=(
            "Preview or explicitly confirm creation of a draft project lead. "
            "An existing thirdparty_id and sales stage_id are required."
        ),
        annotations=_CREATE_ANNOTATIONS,
    )
    async def dolibarr_lead_create(
        thirdparty_id: PositiveIdentifier,
        title: ShortText,
        stage_id: PositiveIdentifier,
        *,
        description: NoteText | None = None,
        amount: Amount | None = None,
        probability_percent: Probability | None = None,
        date_start: date | None = None,
        date_end: date | None = None,
        public_note: NoteText | None = None,
        private_note: NoteText | None = None,
        apply: bool = False,
        confirmation_token: ConfirmationToken | None = None,
    ) -> MutationResponse:
        data = LeadCreateInput(
            thirdparty_id=thirdparty_id,
            title=title,
            stage_id=stage_id,
            description=description,
            amount=amount,
            probability_percent=probability_percent,
            date_start=date_start,
            date_end=date_end,
            public_note=public_note,
            private_note=private_note,
        )
        return _mutation_response(
            await sales.lead_create(
                get_request_api_key(),
                data,
                apply=apply,
                confirmation_token=confirmation_token,
            )
        )

    @server.tool(
        name="dolibarr_lead_update",
        description=(
            "Preview or explicitly confirm an allowlisted lead update. "
            "Sales stage, project state, lead flag, and owner are handled by dedicated tools."
        ),
        annotations=_MUTATION_ANNOTATIONS,
    )
    async def dolibarr_lead_update(
        project_id: PositiveIdentifier,
        *,
        thirdparty_id: PositiveIdentifier | None = None,
        title: ShortText | None = None,
        description: NoteText | None = None,
        amount: Amount | None = None,
        probability_percent: Probability | None = None,
        date_start: date | None = None,
        date_end: date | None = None,
        public_note: NoteText | None = None,
        private_note: NoteText | None = None,
        apply: bool = False,
        confirmation_token: ConfirmationToken | None = None,
    ) -> MutationResponse:
        data = LeadUpdateInput.model_validate(
            _defined(
                thirdparty_id=thirdparty_id,
                title=title,
                description=description,
                amount=amount,
                probability_percent=probability_percent,
                date_start=date_start,
                date_end=date_end,
                public_note=public_note,
                private_note=private_note,
            )
        )
        return _mutation_response(
            await sales.lead_update(
                get_request_api_key(),
                project_id,
                data,
                apply=apply,
                confirmation_token=confirmation_token,
            )
        )

    @server.tool(
        name="dolibarr_lead_change_status",
        description=(
            "Preview or explicitly confirm changing only a lead's sales-stage identifier."
        ),
        annotations=_MUTATION_ANNOTATIONS,
    )
    async def dolibarr_lead_change_status(
        project_id: PositiveIdentifier,
        stage_id: PositiveIdentifier,
        *,
        apply: bool = False,
        confirmation_token: ConfirmationToken | None = None,
    ) -> MutationResponse:
        return _mutation_response(
            await sales.lead_change_status(
                get_request_api_key(),
                project_id,
                stage_id,
                apply=apply,
                confirmation_token=confirmation_token,
            )
        )

    @server.tool(
        name="dolibarr_lead_assign",
        description=(
            "Preview or explicitly confirm replacing internal PROJECTLEADER relations "
            "with one active Dolibarr user."
        ),
        annotations=_MUTATION_ANNOTATIONS,
    )
    async def dolibarr_lead_assign(
        project_id: PositiveIdentifier,
        user_id: PositiveIdentifier,
        *,
        apply: bool = False,
        confirmation_token: ConfirmationToken | None = None,
    ) -> MutationResponse:
        return _mutation_response(
            await sales.lead_assign(
                get_request_api_key(),
                project_id,
                user_id,
                apply=apply,
                confirmation_token=confirmation_token,
            )
        )

    @server.tool(
        name="dolibarr_lead_open_project",
        description=(
            "Preview or explicitly confirm opening a draft or reopening a closed lead project "
            "through Dolibarr's dedicated validate endpoint."
        ),
        annotations=_MUTATION_ANNOTATIONS,
    )
    async def dolibarr_lead_open_project(
        project_id: PositiveIdentifier,
        *,
        apply: bool = False,
        confirmation_token: ConfirmationToken | None = None,
    ) -> MutationResponse:
        return _mutation_response(
            await sales.lead_open_project(
                get_request_api_key(),
                project_id,
                apply=apply,
                confirmation_token=confirmation_token,
            )
        )

    @server.tool(
        name="dolibarr_leave_type_list",
        description="List active Dolibarr leave types available for new requests.",
        annotations=_READ_ONLY_ANNOTATIONS,
    )
    async def dolibarr_leave_type_list() -> LeaveTypeListResult:
        return await leave_requests.leave_type_list(get_request_api_key())

    @server.tool(
        name="dolibarr_leave_request_search",
        description=(
            "Search accessible leave requests using fixed employee, status, and date filters."
        ),
        annotations=_READ_ONLY_ANNOTATIONS,
    )
    async def dolibarr_leave_request_search(
        *,
        employee_id: PositiveIdentifier | None = None,
        status: LeaveRequestStatus | None = None,
        date_from: date | None = None,
        date_to: date | None = None,
        offset: ResultOffset = 0,
        limit: OutputLimit = 200,
    ) -> LeaveRequestSearchResult:
        return await leave_requests.search(
            get_request_api_key(),
            employee_id=employee_id,
            status=status,
            date_from=date_from,
            date_to=date_to,
            offset=offset,
            limit=limit,
        )

    @server.tool(
        name="dolibarr_leave_request_get",
        description="Return allowlisted details for one accessible Dolibarr leave request.",
        annotations=_READ_ONLY_ANNOTATIONS,
    )
    async def dolibarr_leave_request_get(
        request_id: PositiveIdentifier,
    ) -> LeaveRequestDetail:
        return await leave_requests.get(get_request_api_key(), request_id)

    @server.tool(
        name="dolibarr_leave_request_create",
        description=(
            "Preview or explicitly confirm creation of a draft leave request. "
            "Dolibarr remains authoritative for balance, overlap, approver, and permissions."
        ),
        annotations=_CREATE_ANNOTATIONS,
    )
    async def dolibarr_leave_request_create(
        employee_id: PositiveIdentifier,
        leave_type_id: PositiveIdentifier,
        date_start: date,
        date_end: date,
        *,
        half_day_mode: LeaveHalfDayMode = "full_days",
        approver_user_id: PositiveIdentifier | None = None,
        description: NoteText | None = None,
        apply: bool = False,
        confirmation_token: ConfirmationToken | None = None,
    ) -> MutationResponse:
        data = LeaveRequestCreateInput(
            employee_id=employee_id,
            leave_type_id=leave_type_id,
            date_start=date_start,
            date_end=date_end,
            half_day_mode=half_day_mode,
            approver_user_id=approver_user_id,
            description=description,
        )
        return _mutation_response(
            await leave_requests.create(
                get_request_api_key(),
                data,
                apply=apply,
                confirmation_token=confirmation_token,
            )
        )

    @server.tool(
        name="dolibarr_leave_request_update",
        description=(
            "Preview or explicitly confirm an allowlisted update to a draft leave request."
        ),
        annotations=_MUTATION_ANNOTATIONS,
    )
    async def dolibarr_leave_request_update(
        request_id: PositiveIdentifier,
        *,
        employee_id: PositiveIdentifier | None = None,
        leave_type_id: PositiveIdentifier | None = None,
        date_start: date | None = None,
        date_end: date | None = None,
        half_day_mode: LeaveHalfDayMode | None = None,
        approver_user_id: PositiveIdentifier | None = None,
        description: NoteText | None = None,
        apply: bool = False,
        confirmation_token: ConfirmationToken | None = None,
    ) -> MutationResponse:
        data = LeaveRequestUpdateInput.model_validate(
            _defined(
                employee_id=employee_id,
                leave_type_id=leave_type_id,
                date_start=date_start,
                date_end=date_end,
                half_day_mode=half_day_mode,
                approver_user_id=approver_user_id,
                description=description,
            )
        )
        return _mutation_response(
            await leave_requests.update(
                get_request_api_key(),
                request_id,
                data,
                apply=apply,
                confirmation_token=confirmation_token,
            )
        )

    @server.tool(
        name="dolibarr_leave_request_submit",
        description=(
            "Preview or explicitly confirm submission of a draft leave request for approval."
        ),
        annotations=_MUTATION_ANNOTATIONS,
    )
    async def dolibarr_leave_request_submit(
        request_id: PositiveIdentifier,
        *,
        apply: bool = False,
        confirmation_token: ConfirmationToken | None = None,
    ) -> MutationResponse:
        return _mutation_response(
            await leave_requests.submit(
                get_request_api_key(),
                request_id,
                apply=apply,
                confirmation_token=confirmation_token,
            )
        )

    @server.tool(
        name="dolibarr_leave_request_approve",
        description=(
            "Preview or explicitly confirm approval of a submitted leave request. "
            "The preview warns that Dolibarr's balance and policy checks remain authoritative."
        ),
        annotations=_MUTATION_ANNOTATIONS,
    )
    async def dolibarr_leave_request_approve(
        request_id: PositiveIdentifier,
        *,
        apply: bool = False,
        confirmation_token: ConfirmationToken | None = None,
    ) -> MutationResponse:
        return _mutation_response(
            await leave_requests.approve(
                get_request_api_key(),
                request_id,
                apply=apply,
                confirmation_token=confirmation_token,
            )
        )

    @server.tool(
        name="dolibarr_leave_request_refuse",
        description=(
            "Preview or explicitly confirm refusal of a submitted leave request with a reason."
        ),
        annotations=_MUTATION_ANNOTATIONS,
    )
    async def dolibarr_leave_request_refuse(
        request_id: PositiveIdentifier,
        refusal_reason: RequiredNote,
        *,
        apply: bool = False,
        confirmation_token: ConfirmationToken | None = None,
    ) -> MutationResponse:
        return _mutation_response(
            await leave_requests.refuse(
                get_request_api_key(),
                request_id,
                refusal_reason,
                apply=apply,
                confirmation_token=confirmation_token,
            )
        )

    @server.tool(
        name="dolibarr_leave_request_cancel",
        description=(
            "Preview or explicitly confirm cancellation of a submitted or approved leave request."
        ),
        annotations=_MUTATION_ANNOTATIONS,
    )
    async def dolibarr_leave_request_cancel(
        request_id: PositiveIdentifier,
        *,
        apply: bool = False,
        confirmation_token: ConfirmationToken | None = None,
    ) -> MutationResponse:
        return _mutation_response(
            await leave_requests.cancel(
                get_request_api_key(),
                request_id,
                apply=apply,
                confirmation_token=confirmation_token,
            )
        )

    @server.tool(
        name="dolibarr_leave_request_reopen",
        description=(
            "Preview or explicitly confirm reopening a canceled leave request to submitted state."
        ),
        annotations=_MUTATION_ANNOTATIONS,
    )
    async def dolibarr_leave_request_reopen(
        request_id: PositiveIdentifier,
        *,
        apply: bool = False,
        confirmation_token: ConfirmationToken | None = None,
    ) -> MutationResponse:
        return _mutation_response(
            await leave_requests.reopen(
                get_request_api_key(),
                request_id,
                apply=apply,
                confirmation_token=confirmation_token,
            )
        )

    return server
