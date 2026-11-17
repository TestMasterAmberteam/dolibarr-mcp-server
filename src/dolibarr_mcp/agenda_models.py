"""Separate allowlisted project/history models; legacy lead wire schemas remain unchanged."""

from __future__ import annotations

from datetime import date, datetime  # noqa: TC003 - Pydantic resolves schemas at runtime
from typing import Literal

from pydantic import AliasChoices, BaseModel, ConfigDict, Field, field_validator, model_validator

from dolibarr_mcp.agenda_dates import event_dates
from dolibarr_mcp.models import (  # noqa: TC001 - Pydantic resolves schemas at runtime
    MutationChange,
    MutationOutcome,
    NonEmptyString,
    ProjectState,
)

AgendaEventType = Literal["email", "phone_call", "meeting", "other"]
AgendaCompletion = Literal["not_started", "in_progress", "done", "not_applicable"]


class ProjectSummary(BaseModel):
    """A project regardless of opportunity usage or lifecycle state."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    project_id: int = Field(gt=0)
    ref: NonEmptyString
    title: NonEmptyString
    thirdparty_id: int | None = Field(default=None, gt=0)
    project_state: ProjectState
    usage_opportunity: bool
    date_start: date | None = None
    date_end: date | None = None


class ProjectReadDetail(ProjectSummary):
    """Safe details for ordinary projects and opportunities, distinct from lead tools."""

    description: str | None = Field(default=None, max_length=4000)
    public_note: str | None = Field(default=None, max_length=4000)
    private_note: str | None = Field(default=None, max_length=4000)


class ProjectSearchResult(BaseModel):
    """A complete-count page of readable project matches within the scan bound."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    total_count: int = Field(ge=0)
    returned_count: int = Field(ge=0)
    offset: int = Field(ge=0)
    limit: int = Field(gt=0)
    has_more: bool
    rows: list[ProjectSummary]


class DolibarrAgendaEventPayload(BaseModel):
    """No email recipients, assigned-user objects, extrafields, or raw linked objects."""

    model_config = ConfigDict(extra="ignore", populate_by_name=True)

    event_id: int = Field(alias="id", gt=0)
    title: str = Field(alias="label", max_length=128)
    type_code: str = Field(min_length=1, max_length=64)
    type_label: str | None = Field(default=None, max_length=255)
    type_kind: str | None = Field(default=None, alias="type", max_length=64)
    event_code: str | None = Field(default=None, alias="code", max_length=128)
    date_start: int | None = Field(default=None, alias="datep", ge=0, le=4_102_444_799)
    date_end: int | None = Field(
        default=None, validation_alias=AliasChoices("datef", "datep2"), ge=0, le=4_102_444_799
    )
    all_day: bool = Field(default=False, alias="fulldayevent")
    percentage: int = Field(ge=-1, le=100)
    owner_user_id: int | None = Field(default=None, alias="userownerid", gt=0)
    project_id: int | None = Field(default=None, alias="fk_project", gt=0)
    thirdparty_id: int | None = Field(
        default=None, validation_alias=AliasChoices("socid", "fk_soc"), gt=0
    )
    description: str | None = Field(default=None, alias="note_private", max_length=65_535)
    public_note: str | None = Field(default=None, alias="note_public", max_length=65_535)
    location: str | None = Field(default=None, max_length=128)
    modified_at: int | str | None = Field(
        default=None, validation_alias=AliasChoices("datem", "tms", "date_modification")
    )
    element_id: int | None = Field(default=None, alias="fk_element", gt=0)
    element_type: str | None = Field(default=None, alias="elementtype", max_length=128)

    @field_validator(
        "owner_user_id",
        "project_id",
        "thirdparty_id",
        "element_id",
        mode="before",
    )
    @classmethod
    def optional_sentinel(cls, value: object) -> object:
        """Dolibarr uses null, empty strings, and zero for unset optional links/dates."""
        return None if value in (None, "", 0, "0", -1, "-1") else value

    @field_validator("date_start", "date_end", mode="before")
    @classmethod
    def optional_timestamp(cls, value: object) -> object:
        """Keep Unix epoch zero distinct from absent timestamps."""
        return None if value in (None, "", -1, "-1") else value


class AgendaEventSummary(BaseModel):
    """Allowlisted history row interpreted in the explicitly requested timezone."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    event_id: int = Field(gt=0)
    title: str = Field(max_length=128)
    type_code: str = Field(max_length=64)
    type_label: str | None = None
    event_code: str | None = None
    automatic: bool
    date_start: datetime | None
    date_end: datetime | None
    timezone: str
    all_day: bool
    percentage: int = Field(ge=-1, le=100)
    owner_user_id: int | None
    project_id: int | None
    thirdparty_id: int | None


class AgendaEventDetail(AgendaEventSummary):
    """Bounded text without raw upstream fields or mail addresses."""

    description: str | None = Field(default=None, max_length=4000)
    public_note: str | None = Field(default=None, max_length=4000)
    location: str | None = None


class AgendaEventSearchResult(BaseModel):
    """Readable history in ascending event-ID order; never silently truncated."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    total_count: int = Field(ge=0)
    returned_count: int = Field(ge=0)
    offset: int = Field(ge=0)
    limit: int = Field(gt=0)
    has_more: bool
    rows: list[AgendaEventSummary]


class AgendaEventCreateInput(BaseModel):
    """A manual own event, not email dispatch or a project-note update."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    title: str = Field(min_length=1, max_length=128)
    event_type: AgendaEventType
    project_id: int = Field(gt=0)
    start: str = Field(min_length=10, max_length=64)
    end: str | None = Field(default=None, min_length=10, max_length=64)
    timezone: str = Field(default="UTC", min_length=1, max_length=128)
    all_day: bool | None = None
    status: AgendaCompletion = "not_started"
    description: str | None = Field(default=None, max_length=4000)
    thirdparty_id: int | None = Field(default=None, gt=0)

    @field_validator("title")
    @classmethod
    def nonblank_title(cls, value: str) -> str:
        """Reject empty labels without silently altering the confirmed text."""
        if not value.strip():
            message = "Agenda title must not be blank."
            raise ValueError(message)
        return value

    @model_validator(mode="after")
    def valid_dates(self) -> AgendaEventCreateInput:
        """Validate local dates before any upstream request."""
        event_dates(self.start, self.end, self.timezone, self.all_day)
        return self


class AgendaMutationResponse(BaseModel):
    """The existing flat preview/result convention with an optional uncertain event ID."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    operation: Literal["agenda_event_create", "agenda_event_delete"]
    phase: Literal["preview", "result"]
    target_kind: Literal["agenda_event"] = "agenda_event"
    target_id: int | None = Field(default=None, gt=0)
    changes: list[MutationChange] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    confirmation_token: str | None = Field(default=None, min_length=64, max_length=64)
    apply: bool = False
    outcome: MutationOutcome | None = None
    partial_errors: list[str] = Field(default_factory=list)
    agenda_event: AgendaEventDetail | None = None
    matching_event_ids: list[int] = Field(default_factory=list)
