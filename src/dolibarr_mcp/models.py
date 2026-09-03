"""Validated upstream identity, time-entry, and public reporting models."""

from __future__ import annotations

from datetime import date  # noqa: TC003 - Pydantic resolves report fields at runtime
from typing import Annotated, Literal

from pydantic import (
    AliasChoices,
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    field_validator,
    model_validator,
)

NonEmptyString = Annotated[str, StringConstraints(min_length=1, max_length=255)]
SummaryGroup = Literal["user", "project", "task", "month", "week"]
CustomerStatus = Literal["neutral", "customer", "prospect", "customer_and_prospect"]
ProjectState = Literal["draft", "open", "closed"]
LeaveRequestStatus = Literal["draft", "submitted", "approved", "canceled", "refused"]
LeaveHalfDayMode = Literal[
    "full_days",
    "start_afternoon_end_afternoon",
    "start_morning_end_morning",
    "start_afternoon_end_morning",
]
MutationOutcome = Literal["applied", "no_op", "partial"]
_DOLIBARR_TEXT_MAX_BYTES = (1 << 16) - 1


def _blank_to_none(value: object) -> object:
    return None if value == "" else value


def _bounded_utf8_text(value: object, *, max_bytes: int) -> object:
    """Return text within a byte budget without leaving a partial UTF-8 character."""
    if not isinstance(value, str):
        return value
    encoded = value.encode("utf-8", errors="replace")
    return encoded[:max_bytes].decode("utf-8", errors="ignore")


def _prefer_project_opportunity_status(value: object) -> object:
    """Prefer Dolibarr's API property while retaining a legacy-field fallback."""
    if not isinstance(value, dict):
        return value
    normalized = dict(value)
    opportunity_status = normalized.get("opp_status")
    legacy_status = normalized.get("fk_opp_status")
    if opportunity_status in (None, "") and legacy_status not in (None, ""):
        normalized["opp_status"] = legacy_status
    return normalized


class DolibarrUserPayload(BaseModel):
    """The minimum accepted subset of Dolibarr's ``/users/info`` response."""

    model_config = ConfigDict(extra="ignore", populate_by_name=True)

    id: int
    login: NonEmptyString
    first_name: str | None = Field(default=None, alias="firstname", max_length=255)
    last_name: str | None = Field(default=None, alias="lastname", max_length=255)
    status: int = Field(
        default=1,
        validation_alias=AliasChoices("status", "statut"),
        ge=0,
        le=1,
    )

    def to_identity(self) -> VerifiedIdentity:
        """Discard every upstream property outside the public allowlist."""
        return VerifiedIdentity(
            user_id=self.id,
            login=self.login,
            first_name=self.first_name,
            last_name=self.last_name,
        )


class VerifiedIdentity(BaseModel):
    """Safe, request-scoped identity exposed by ``dolibarr_whoami``."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    user_id: int
    login: NonEmptyString
    first_name: str | None = Field(default=None, max_length=255)
    last_name: str | None = Field(default=None, max_length=255)


class DolibarrTimeEntryPayload(BaseModel):
    """Allowlisted subset of one Dolibarr task time-spent line."""

    model_config = ConfigDict(extra="ignore", populate_by_name=True)

    entry_id: int = Field(alias="timespent_line_id", gt=0)
    timestamp: int = Field(alias="timespent_line_date", ge=0)
    timestamp_with_hour: int | None = Field(
        default=None,
        alias="timespent_line_datehour",
        ge=0,
    )
    duration_seconds: int = Field(alias="timespent_line_duration", ge=0)
    user_id: int = Field(alias="timespent_line_fk_user", gt=0)
    product_id: int | None = Field(
        default=None,
        alias="timespent_line_fk_product",
        gt=0,
    )
    note: str | None = Field(default=None, alias="timespent_line_note")
    project_id: int | None = Field(default=None, alias="fk_project", gt=0)
    project_ref: str | None = Field(default=None, max_length=255)
    project_label: str | None = Field(default=None, max_length=255)
    task_id: int = Field(alias="fk_task", gt=0)
    task_ref: str | None = Field(default=None, max_length=255)
    task_label: str | None = Field(default=None, max_length=255)

    @field_validator("timestamp_with_hour", "project_id", mode="before")
    @classmethod
    def blank_optional_integer(cls, value: object) -> object:
        """Treat Dolibarr's optional empty-string identifiers as absent."""
        return _blank_to_none(value)

    @field_validator("product_id", mode="before")
    @classmethod
    def absent_product_identifier(cls, value: object) -> object:
        """Treat Dolibarr's empty or non-positive product identifier as unassigned."""
        if value is None or value == "":
            return None
        if isinstance(value, int) and value <= 0:
            return None
        if isinstance(value, str):
            try:
                return None if int(value) <= 0 else value
            except ValueError:
                return value
        return value


class DolibarrTaskPayload(BaseModel):
    """Allowlisted task metadata and optional detailed time lines."""

    model_config = ConfigDict(extra="ignore", populate_by_name=True)

    task_id: int = Field(alias="id", gt=0)
    project_id: int = Field(alias="fk_project", gt=0)
    ref: NonEmptyString
    label: NonEmptyString
    lines: list[DolibarrTimeEntryPayload] | None = None


class DolibarrProjectPayload(BaseModel):
    """Allowlisted project metadata used by reporting and sales tools."""

    model_config = ConfigDict(extra="ignore", populate_by_name=True)

    project_id: int = Field(alias="id", gt=0)
    ref: NonEmptyString
    label: NonEmptyString = Field(alias="title")
    thirdparty_id: int | None = Field(
        default=None,
        validation_alias=AliasChoices("socid", "fk_soc"),
        gt=0,
    )
    status: int = Field(default=0, ge=0, le=2)
    usage_opportunity: bool = False
    stage_id: int | None = Field(
        default=None,
        validation_alias=AliasChoices("opp_status", "fk_opp_status"),
        gt=0,
    )
    stage_code: str | None = Field(default=None, alias="opp_status_code", max_length=64)
    description: str | None = Field(default=None, max_length=_DOLIBARR_TEXT_MAX_BYTES)
    amount: float | None = Field(default=None, alias="opp_amount", ge=0)
    probability_percent: float | None = Field(default=None, alias="opp_percent", ge=0, le=100)
    date_start: int | None = Field(default=None, ge=0)
    date_end: int | None = Field(default=None, ge=0)
    public_note: str | None = Field(default=None, alias="note_public")
    private_note: str | None = Field(default=None, alias="note_private")
    modified_at: str | None = Field(
        default=None,
        validation_alias=AliasChoices("tms", "date_modification"),
        max_length=64,
    )

    @model_validator(mode="before")
    @classmethod
    def prefer_project_opportunity_status(cls, value: object) -> object:
        """Do not let an empty database-field alias hide the API stage value."""
        return _prefer_project_opportunity_status(value)

    @field_validator(
        "thirdparty_id",
        "stage_id",
        "amount",
        "probability_percent",
        "date_start",
        "date_end",
        "modified_at",
        mode="before",
    )
    @classmethod
    def blank_project_value(cls, value: object) -> object:
        """Treat Dolibarr's empty optional project values as absent."""
        return _blank_to_none(value)

    @field_validator("description", mode="before")
    @classmethod
    def bounded_project_description(cls, value: object) -> object:
        """Bound Dolibarr TEXT without rejecting an otherwise valid project page."""
        return _bounded_utf8_text(value, max_bytes=_DOLIBARR_TEXT_MAX_BYTES)


class DolibarrLeadStageObservationPayload(BaseModel):
    """Minimal project projection used to discover stages on accessible leads."""

    model_config = ConfigDict(extra="ignore", populate_by_name=True)

    project_id: int = Field(alias="id", gt=0)
    usage_opportunity: bool = False
    stage_id: int | None = Field(
        default=None,
        validation_alias=AliasChoices("opp_status", "fk_opp_status"),
        gt=0,
    )
    stage_code: str | None = Field(default=None, alias="opp_status_code", max_length=64)

    @model_validator(mode="before")
    @classmethod
    def prefer_project_opportunity_status(cls, value: object) -> object:
        """Do not let an empty database-field alias hide the API stage value."""
        return _prefer_project_opportunity_status(value)

    @field_validator("stage_id", "stage_code", mode="before")
    @classmethod
    def blank_stage_value(cls, value: object) -> object:
        """Treat Dolibarr's empty optional stage values as absent."""
        return _blank_to_none(value)


class DolibarrThirdpartyPayload(BaseModel):
    """Allowlisted subset of a Dolibarr third-party object."""

    model_config = ConfigDict(extra="ignore", populate_by_name=True)

    thirdparty_id: int = Field(alias="id", gt=0)
    name: NonEmptyString
    alias: str | None = Field(default=None, alias="name_alias", max_length=255)
    address: str | None = Field(default=None, max_length=1000)
    postal_code: str | None = Field(default=None, alias="zip", max_length=32)
    city: str | None = Field(default=None, alias="town", max_length=255)
    country_code: str | None = Field(default=None, max_length=3)
    email: str | None = Field(default=None, max_length=320)
    phone: str | None = Field(default=None, max_length=64)
    vat_number: str | None = Field(default=None, alias="tva_intra", max_length=64)
    customer_classification: int = Field(default=0, alias="client", ge=0, le=3)
    public_note: str | None = Field(default=None, alias="note_public")
    private_note: str | None = Field(default=None, alias="note_private")
    modified_at: str | None = Field(
        default=None,
        validation_alias=AliasChoices("tms", "date_modification"),
        max_length=64,
    )

    @field_validator(
        "alias",
        "address",
        "postal_code",
        "city",
        "country_code",
        "email",
        "phone",
        "vat_number",
        "modified_at",
        mode="before",
    )
    @classmethod
    def blank_thirdparty_value(cls, value: object) -> object:
        """Normalize empty optional values returned by Dolibarr."""
        return _blank_to_none(value)


class DolibarrProjectContactPayload(BaseModel):
    """Allowlisted project contact relation returned by Dolibarr."""

    model_config = ConfigDict(extra="ignore", populate_by_name=True)

    contact_id: int = Field(alias="id", gt=0)
    row_id: int | None = Field(default=None, alias="rowid", gt=0)
    code: NonEmptyString
    source: str | None = Field(default=None, max_length=32)
    login: str | None = Field(default=None, max_length=255)
    first_name: str | None = Field(default=None, alias="firstname", max_length=255)
    last_name: str | None = Field(default=None, alias="lastname", max_length=255)

    @field_validator("row_id", "source", "login", "first_name", "last_name", mode="before")
    @classmethod
    def blank_contact_value(cls, value: object) -> object:
        """Normalize empty optional contact relation values."""
        return _blank_to_none(value)


class DolibarrLeaveRequestPayload(BaseModel):
    """Allowlisted subset of one Dolibarr leave request."""

    model_config = ConfigDict(extra="ignore", populate_by_name=True)

    request_id: int = Field(alias="id", gt=0)
    reference: str | int | None = Field(default=None, alias="ref")
    employee_id: int = Field(alias="fk_user", gt=0)
    approver_user_id: int | None = Field(default=None, alias="fk_validator", gt=0)
    leave_type_id: int = Field(alias="fk_type", gt=0)
    start_timestamp: int = Field(alias="date_debut", ge=0)
    end_timestamp: int = Field(alias="date_fin", ge=0)
    half_day_code: int = Field(default=0, alias="halfday", ge=-1, le=2)
    status_code: int = Field(
        validation_alias=AliasChoices("status", "statut"),
        ge=1,
        le=5,
    )
    description: str | None = Field(default=None, max_length=4000)
    refusal_reason: str | None = Field(default=None, alias="detail_refuse", max_length=4000)

    @field_validator(
        "approver_user_id",
        "reference",
        "description",
        "refusal_reason",
        mode="before",
    )
    @classmethod
    def blank_leave_value(cls, value: object) -> object:
        """Normalize empty optional leave-request values."""
        return _blank_to_none(value)


class DolibarrLeaveTypePayload(BaseModel):
    """Allowlisted active leave type from Dolibarr's setup dictionary."""

    model_config = ConfigDict(extra="ignore", populate_by_name=True)

    type_id: int = Field(validation_alias=AliasChoices("id", "rowid"), gt=0)
    code: NonEmptyString
    label: NonEmptyString
    active: bool = True
    affects_balance: bool = Field(default=False, alias="affect")
    block_if_negative: bool = False
    sort_order: int = Field(
        default=0,
        validation_alias=AliasChoices("sortorder", "sort_order"),
        ge=0,
    )


class UserDescriptor(BaseModel):
    """Safe user label embedded in authorized report output."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    user_id: int = Field(gt=0)
    login: NonEmptyString | None = None
    first_name: str | None = Field(default=None, max_length=255)
    last_name: str | None = Field(default=None, max_length=255)


class ProjectDescriptor(BaseModel):
    """Safe project label embedded in authorized report output."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    project_id: int = Field(gt=0)
    ref: str | None = Field(default=None, max_length=255)
    label: str | None = Field(default=None, max_length=255)


class TaskDescriptor(BaseModel):
    """Safe task label embedded in authorized report output."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    task_id: int = Field(gt=0)
    ref: str | None = Field(default=None, max_length=255)
    label: str | None = Field(default=None, max_length=255)


class TimeEntry(BaseModel):
    """One normalized, allowlisted time-spent record."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    entry_id: int = Field(gt=0)
    day: date
    duration_seconds: int = Field(ge=0)
    duration_hours: float = Field(ge=0)
    user: UserDescriptor
    project: ProjectDescriptor
    task: TaskDescriptor
    product_id: int | None = Field(default=None, gt=0)
    note: str | None = Field(default=None, max_length=4000)


class DurationTotal(BaseModel):
    """Reusable exact-seconds and display-hours total."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    duration_seconds: int = Field(ge=0)
    duration_hours: float = Field(ge=0)


class MyTimeReportRow(DurationTotal):
    """Current user's time grouped by day, project, and task."""

    day: date
    project: ProjectDescriptor
    task: TaskDescriptor


class MyTimeReport(DurationTotal):
    """Bounded current-user time report for an inclusive date range."""

    user: UserDescriptor
    date_from: date
    date_to: date
    group_count: int = Field(ge=0)
    returned_group_count: int = Field(ge=0)
    truncated: bool
    rows: list[MyTimeReportRow]


class UserTimeTotal(DurationTotal):
    """Project time grouped by user."""

    user: UserDescriptor


class TaskTimeTotal(DurationTotal):
    """Project time grouped by task."""

    task: TaskDescriptor


class ProjectTimeReport(DurationTotal):
    """Bounded project report grouped independently by user and task."""

    project: ProjectDescriptor
    date_from: date
    date_to: date
    entry_count: int = Field(ge=0)
    user_group_count: int = Field(ge=0)
    task_group_count: int = Field(ge=0)
    truncated: bool
    by_user: list[UserTimeTotal]
    by_task: list[TaskTimeTotal]


class TaskTimespentReport(DurationTotal):
    """Paged detailed time entries for one task."""

    task: TaskDescriptor
    project: ProjectDescriptor
    date_from: date
    date_to: date
    entry_count: int = Field(ge=0)
    returned_entry_count: int = Field(ge=0)
    offset: int = Field(ge=0)
    limit: int = Field(gt=0)
    has_more: bool
    entries: list[TimeEntry]


class TimeSummaryRow(DurationTotal):
    """One generic aggregation bucket."""

    key: NonEmptyString
    label: NonEmptyString


class TimeSummary(DurationTotal):
    """Bounded aggregation over authorized time entries."""

    group_by: SummaryGroup
    date_from: date
    date_to: date
    entry_count: int = Field(ge=0)
    group_count: int = Field(ge=0)
    returned_group_count: int = Field(ge=0)
    truncated: bool
    rows: list[TimeSummaryRow]


class TimeEntriesReport(DurationTotal):
    """Paged raw authorized time entries with optional fixed filters."""

    date_from: date
    date_to: date
    entry_count: int = Field(ge=0)
    returned_entry_count: int = Field(ge=0)
    offset: int = Field(ge=0)
    limit: int = Field(gt=0)
    has_more: bool
    entries: list[TimeEntry]


class ThirdpartyCreateInput(BaseModel):
    """Allowlisted fields for creating one Dolibarr third party."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    name: NonEmptyString
    customer_status: CustomerStatus
    alias: str | None = Field(default=None, max_length=255)
    address: str | None = Field(default=None, max_length=1000)
    postal_code: str | None = Field(default=None, max_length=32)
    city: str | None = Field(default=None, max_length=255)
    country_code: str | None = Field(default=None, min_length=2, max_length=3)
    email: str | None = Field(default=None, max_length=320)
    phone: str | None = Field(default=None, max_length=64)
    vat_number: str | None = Field(default=None, max_length=64)
    public_note: str | None = Field(default=None, max_length=4000)
    private_note: str | None = Field(default=None, max_length=4000)


class ThirdpartyUpdateInput(BaseModel):
    """Allowlisted partial update for one Dolibarr third party."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    name: NonEmptyString | None = None
    customer_status: CustomerStatus | None = None
    alias: str | None = Field(default=None, max_length=255)
    address: str | None = Field(default=None, max_length=1000)
    postal_code: str | None = Field(default=None, max_length=32)
    city: str | None = Field(default=None, max_length=255)
    country_code: str | None = Field(default=None, min_length=2, max_length=3)
    email: str | None = Field(default=None, max_length=320)
    phone: str | None = Field(default=None, max_length=64)
    vat_number: str | None = Field(default=None, max_length=64)
    public_note: str | None = Field(default=None, max_length=4000)
    private_note: str | None = Field(default=None, max_length=4000)

    @model_validator(mode="after")
    def require_update(self) -> ThirdpartyUpdateInput:
        """Reject empty updates before any upstream request is made."""
        if not self.model_fields_set:
            msg = "At least one third-party field must be supplied"
            raise ValueError(msg)
        return self


class LeadCreateInput(BaseModel):
    """Allowlisted fields for creating one draft project lead."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    thirdparty_id: int = Field(gt=0)
    title: NonEmptyString
    stage_id: int = Field(gt=0)
    description: str | None = Field(default=None, max_length=4000)
    amount: float | None = Field(default=None, ge=0)
    probability_percent: float | None = Field(default=None, ge=0, le=100)
    date_start: date | None = None
    date_end: date | None = None
    public_note: str | None = Field(default=None, max_length=4000)
    private_note: str | None = Field(default=None, max_length=4000)

    @model_validator(mode="after")
    def validate_dates(self) -> LeadCreateInput:
        """Require an ordered optional project date range."""
        if (
            self.date_start is not None
            and self.date_end is not None
            and self.date_start > self.date_end
        ):
            msg = "date_start must not be later than date_end"
            raise ValueError(msg)
        return self


class LeadUpdateInput(BaseModel):
    """Allowlisted partial update that cannot alter lead control fields."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    thirdparty_id: int | None = Field(default=None, gt=0)
    title: NonEmptyString | None = None
    description: str | None = Field(default=None, max_length=4000)
    amount: float | None = Field(default=None, ge=0)
    probability_percent: float | None = Field(default=None, ge=0, le=100)
    date_start: date | None = None
    date_end: date | None = None
    public_note: str | None = Field(default=None, max_length=4000)
    private_note: str | None = Field(default=None, max_length=4000)

    @model_validator(mode="after")
    def validate_update(self) -> LeadUpdateInput:
        """Reject empty updates and reversed supplied date ranges."""
        if not self.model_fields_set:
            msg = "At least one lead field must be supplied"
            raise ValueError(msg)
        if (
            self.date_start is not None
            and self.date_end is not None
            and self.date_start > self.date_end
        ):
            msg = "date_start must not be later than date_end"
            raise ValueError(msg)
        return self


class LeaveRequestCreateInput(BaseModel):
    """Allowlisted fields for creating one draft leave request."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    employee_id: int = Field(gt=0)
    leave_type_id: int = Field(gt=0)
    date_start: date
    date_end: date
    half_day_mode: LeaveHalfDayMode = "full_days"
    approver_user_id: int | None = Field(default=None, gt=0)
    description: str | None = Field(default=None, max_length=4000)

    @model_validator(mode="after")
    def validate_dates(self) -> LeaveRequestCreateInput:
        """Require an ordered leave date range."""
        if self.date_start > self.date_end:
            msg = "date_start must not be later than date_end"
            raise ValueError(msg)
        return self


class LeaveRequestUpdateInput(BaseModel):
    """Allowlisted partial update for one draft leave request."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    employee_id: int | None = Field(default=None, gt=0)
    leave_type_id: int | None = Field(default=None, gt=0)
    date_start: date | None = None
    date_end: date | None = None
    half_day_mode: LeaveHalfDayMode | None = None
    approver_user_id: int | None = Field(default=None, gt=0)
    description: str | None = Field(default=None, max_length=4000)

    @model_validator(mode="after")
    def validate_update(self) -> LeaveRequestUpdateInput:
        """Reject empty updates and reversed supplied date ranges."""
        if not self.model_fields_set:
            msg = "At least one leave-request field must be supplied"
            raise ValueError(msg)
        if (
            self.date_start is not None
            and self.date_end is not None
            and self.date_start > self.date_end
        ):
            msg = "date_start must not be later than date_end"
            raise ValueError(msg)
        return self


class UserSummary(BaseModel):
    """Safe user identity used for lookup and project ownership."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    user_id: int = Field(gt=0)
    login: NonEmptyString | None = None
    first_name: str | None = Field(default=None, max_length=255)
    last_name: str | None = Field(default=None, max_length=255)


class LeaveTypeSummary(BaseModel):
    """One active leave type safe to select for a request."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    leave_type_id: int = Field(gt=0)
    code: NonEmptyString
    label: NonEmptyString
    affects_balance: bool
    block_if_negative: bool


class LeaveTypeListResult(BaseModel):
    """Bounded list of active Dolibarr leave types."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    count: int = Field(ge=0)
    rows: list[LeaveTypeSummary]


class LeaveRequestSummary(BaseModel):
    """Allowlisted leave-request row returned by search."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    request_id: int = Field(gt=0)
    reference: str | None = Field(default=None, max_length=255)
    employee_id: int = Field(gt=0)
    approver_user_id: int | None = Field(default=None, gt=0)
    leave_type_id: int = Field(gt=0)
    status: LeaveRequestStatus
    date_start: date
    date_end: date
    half_day_mode: LeaveHalfDayMode


class LeaveRequestDetail(LeaveRequestSummary):
    """Detailed authorized leave request with bounded free text."""

    description: str | None = Field(default=None, max_length=4000)
    refusal_reason: str | None = Field(default=None, max_length=4000)


class LeaveRequestSearchResult(BaseModel):
    """Paged leave-request search response."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    total_count: int = Field(ge=0)
    returned_count: int = Field(ge=0)
    offset: int = Field(ge=0)
    limit: int = Field(gt=0)
    has_more: bool
    rows: list[LeaveRequestSummary]


class ThirdpartySummary(BaseModel):
    """Bounded third-party row returned by search."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    thirdparty_id: int = Field(gt=0)
    name: NonEmptyString
    alias: str | None = Field(default=None, max_length=255)
    city: str | None = Field(default=None, max_length=255)
    country_code: str | None = Field(default=None, max_length=3)
    email: str | None = Field(default=None, max_length=320)
    vat_number: str | None = Field(default=None, max_length=64)
    customer_status: CustomerStatus


class ThirdpartyDetail(ThirdpartySummary):
    """Allowlisted detailed third-party view."""

    address: str | None = Field(default=None, max_length=1000)
    postal_code: str | None = Field(default=None, max_length=32)
    phone: str | None = Field(default=None, max_length=64)
    public_note: str | None = Field(default=None, max_length=4000)
    private_note: str | None = Field(default=None, max_length=4000)


class ThirdpartySearchResult(BaseModel):
    """Paged third-party search response."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    total_count: int = Field(ge=0)
    returned_count: int = Field(ge=0)
    offset: int = Field(ge=0)
    limit: int = Field(gt=0)
    has_more: bool
    rows: list[ThirdpartySummary]


class LeadStageSummary(BaseModel):
    """One configured or observed opportunity-stage dictionary row."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    stage_id: int = Field(gt=0)
    stage_code: NonEmptyString | None = None
    label: NonEmptyString | None = None
    aliases: list[NonEmptyString] = Field(default_factory=list, max_length=10)
    probability_percent: float | None = Field(default=None, ge=0, le=100)
    position: int | None = None
    active: bool | None = None
    configured: bool = False
    observed_lead_count: int = Field(ge=0)


class LeadStageListResult(BaseModel):
    """Configured and observed stages; Dolibarr has no complete dictionary API."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    count: int = Field(ge=0)
    complete: Literal[False] = False
    source: Literal["operator_catalog_and_accessible_leads"] = (
        "operator_catalog_and_accessible_leads"
    )
    rows: list[LeadStageSummary]
    warnings: list[str]


class LeadSummary(BaseModel):
    """Bounded project-lead row returned by search."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    project_id: int = Field(gt=0)
    ref: NonEmptyString
    title: NonEmptyString
    thirdparty_id: int | None = Field(default=None, gt=0)
    stage_id: int | None = Field(default=None, gt=0)
    stage_code: str | None = Field(default=None, max_length=64)
    project_state: ProjectState
    amount: float | None = Field(default=None, ge=0)
    probability_percent: float | None = Field(default=None, ge=0, le=100)
    date_start: date | None = None
    date_end: date | None = None
    owners: list[UserSummary] = Field(default_factory=list)


class LeadDetail(LeadSummary):
    """Allowlisted detailed view of a project lead."""

    description: str | None = Field(default=None, max_length=4000)
    public_note: str | None = Field(default=None, max_length=4000)
    private_note: str | None = Field(default=None, max_length=4000)


class LeadSearchResult(BaseModel):
    """Paged lead search response."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    total_count: int = Field(ge=0)
    returned_count: int = Field(ge=0)
    offset: int = Field(ge=0)
    limit: int = Field(gt=0)
    has_more: bool
    rows: list[LeadSummary]


class UserSearchResult(BaseModel):
    """Paged active-user lookup response."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    total_count: int = Field(ge=0)
    returned_count: int = Field(ge=0)
    offset: int = Field(ge=0)
    limit: int = Field(gt=0)
    has_more: bool
    rows: list[UserSummary]


class MutationChange(BaseModel):
    """One normalized field difference shown before a write."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    field: NonEmptyString
    before: str | int | float | bool | date | None = None
    after: str | int | float | bool | date | None = None


class MutationPreview(BaseModel):
    """Stateless preview required before applying a Dolibarr write."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    operation: NonEmptyString
    target_kind: Literal["thirdparty", "lead", "leave_request"]
    target_id: int | None = Field(default=None, gt=0)
    changes: list[MutationChange]
    warnings: list[str]
    confirmation_token: str = Field(min_length=64, max_length=64)
    apply: Literal[False] = False


class MutationResult(BaseModel):
    """Outcome of an explicitly confirmed write."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    operation: NonEmptyString
    outcome: MutationOutcome
    target_id: int = Field(gt=0)
    warnings: list[str] = Field(default_factory=list)
    partial_errors: list[str] = Field(default_factory=list)
    thirdparty: ThirdpartyDetail | None = None
    lead: LeadDetail | None = None
    leave_request: LeaveRequestDetail | None = None


class MutationResponse(BaseModel):
    """Stable, flat MCP response for either a preview or an applied mutation."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    operation: NonEmptyString
    phase: Literal["preview", "result"]
    target_kind: Literal["thirdparty", "lead", "leave_request"]
    target_id: int | None = Field(default=None, gt=0)
    changes: list[MutationChange] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    confirmation_token: str | None = Field(default=None, min_length=64, max_length=64)
    apply: bool
    outcome: MutationOutcome | None = None
    partial_errors: list[str] = Field(default_factory=list)
    thirdparty: ThirdpartyDetail | None = None
    lead: LeadDetail | None = None
    leave_request: LeaveRequestDetail | None = None

    @classmethod
    def from_mutation(
        cls,
        value: MutationPreview | MutationResult,
    ) -> MutationResponse:
        """Normalize internal preview/result variants into one MCP wire shape."""
        if isinstance(value, MutationPreview):
            return cls(
                operation=value.operation,
                phase="preview",
                target_kind=value.target_kind,
                target_id=value.target_id,
                changes=value.changes,
                warnings=value.warnings,
                confirmation_token=value.confirmation_token,
                apply=False,
            )
        return cls(
            operation=value.operation,
            phase="result",
            target_kind=(
                "thirdparty"
                if value.thirdparty is not None
                else "lead"
                if value.lead is not None
                else "leave_request"
            ),
            target_id=value.target_id,
            warnings=value.warnings,
            apply=True,
            outcome=value.outcome,
            partial_errors=value.partial_errors,
            thirdparty=value.thirdparty,
            lead=value.lead,
            leave_request=value.leave_request,
        )
