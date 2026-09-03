"""Domain errors with deliberately non-sensitive public representations."""

from __future__ import annotations


class DolibarrError(Exception):
    """Base class for failures returned by or while contacting Dolibarr."""

    status_code = 502
    public_message = "Dolibarr returned an invalid response."

    def __init__(self, *, retry_after: str | None = None) -> None:
        super().__init__(self.public_message)
        self.retry_after = retry_after

    def __repr__(self) -> str:
        """Exclude upstream details and credentials from diagnostic representations."""
        return f"{type(self).__name__}()"


class InvalidDolibarrCredentialsError(DolibarrError):
    """The presented key is invalid or cannot access the identity endpoint."""

    status_code = 401
    public_message = "Authentication failed."


class DolibarrRateLimitedError(DolibarrError):
    """Dolibarr rejected the request because of rate limiting."""

    status_code = 429
    public_message = "Dolibarr rate limit exceeded."


class DolibarrUnavailableError(DolibarrError):
    """Dolibarr is temporarily unreachable or unavailable."""

    status_code = 503
    public_message = "Dolibarr is temporarily unavailable."


class InvalidDolibarrResponseError(DolibarrError):
    """Dolibarr returned an unexpected status or payload."""


class DolibarrPermissionDeniedError(DolibarrError):
    """The verified user cannot access the requested Dolibarr resource."""

    status_code = 403
    public_message = "Dolibarr denied access to the requested resource."


class DolibarrNotFoundError(DolibarrError):
    """The requested fixed-path Dolibarr resource does not exist."""

    status_code = 404
    public_message = "Requested Dolibarr resource was not found."


class DolibarrResultLimitError(DolibarrError):
    """A report exceeded a local bound before it could be returned safely."""

    status_code = 422
    public_message = "Dolibarr report exceeds the safe processing limit."


class DolibarrConflictError(DolibarrError):
    """Dolibarr rejected a write because the resource state conflicts."""

    status_code = 409
    public_message = "Dolibarr resource state conflicts with the requested change."


class DolibarrWriteRejectedError(DolibarrError):
    """Dolibarr rejected an allowlisted write payload."""

    status_code = 422
    public_message = "Dolibarr rejected the submitted change."


class SalesRequestError(DolibarrError):
    """A sales-tool request is inconsistent after schema validation."""

    status_code = 422
    public_message = "Invalid sales operation parameters."


class SalesStageResolutionError(SalesRequestError):
    """A supplied stage code cannot be mapped to one numeric identifier."""

    public_message = "Sales stage code is not uniquely resolvable."


class SalesInactiveStageError(SalesRequestError):
    """A configured Dolibarr opportunity stage is inactive."""

    public_message = "Configured sales stage is inactive."


class SalesConfirmationError(DolibarrError):
    """A preview token is missing or no longer matches current state."""

    status_code = 409
    public_message = "Sales operation preview is missing or stale."


class LeaveRequestError(DolibarrError):
    """A leave-request operation is inconsistent with the current workflow state."""

    status_code = 422
    public_message = "Invalid leave-request operation parameters or state."


class LeaveConfirmationError(DolibarrError):
    """A leave-request preview token is missing or stale."""

    status_code = 409
    public_message = "Leave-request operation preview is missing or stale."


class ReportRequestError(DolibarrError):
    """A report request is internally inconsistent after schema validation."""

    status_code = 422
    public_message = "Invalid report parameters."
