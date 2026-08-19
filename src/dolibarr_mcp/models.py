"""Validated upstream and public identity models."""

from __future__ import annotations

from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

NonEmptyString = Annotated[str, StringConstraints(min_length=1, max_length=255)]


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
