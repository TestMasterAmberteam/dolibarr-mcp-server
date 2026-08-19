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
    public_message = "Authentication service rate limit exceeded."


class DolibarrUnavailableError(DolibarrError):
    """Dolibarr is temporarily unreachable or unavailable."""

    status_code = 503
    public_message = "Authentication service is temporarily unavailable."


class InvalidDolibarrResponseError(DolibarrError):
    """Dolibarr returned an unexpected status or payload."""
