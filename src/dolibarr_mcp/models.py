"""Validated upstream identity, time-entry, and public reporting models."""

from __future__ import annotations

from datetime import date  # noqa: TC003 - Pydantic resolves report fields at runtime
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, field_validator

NonEmptyString = Annotated[str, StringConstraints(min_length=1, max_length=255)]
SummaryGroup = Literal["user", "project", "task", "month", "week"]


def _blank_to_none(value: object) -> object:
    return None if value == "" else value


class DolibarrUserPayload(BaseModel):
    """The minimum accepted subset of Dolibarr's ``/users/info`` response."""

    model_config = ConfigDict(extra="ignore", populate_by_name=True)

    id: int
    login: NonEmptyString
    first_name: str | None = Field(default=None, alias="firstname", max_length=255)
    last_name: str | None = Field(default=None, alias="lastname", max_length=255)

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
    """Allowlisted project metadata used in report headings."""

    model_config = ConfigDict(extra="ignore", populate_by_name=True)

    project_id: int = Field(alias="id", gt=0)
    ref: NonEmptyString
    label: NonEmptyString = Field(alias="title")


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
