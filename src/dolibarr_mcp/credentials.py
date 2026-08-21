"""Request-scoped handling for one presented Dolibarr API key."""

from __future__ import annotations

from contextvars import ContextVar, Token
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from starlette.types import ASGIApp, Receive, Scope, Send


@dataclass(slots=True)
class RequestCredential:
    """Mutable secret holder that can invalidate copied async-context references."""

    _api_key: str | None = field(default=None, repr=False)

    def store(self, api_key: str) -> None:
        """Store one successfully verified key for the active request."""
        if self._api_key is not None:
            message = "Request credential is already available"
            raise RuntimeError(message)
        self._api_key = api_key

    def get(self) -> str:
        """Return the active key or fail without exposing secret state."""
        if self._api_key is None:
            message = "Request credential is unavailable"
            raise PermissionError(message)
        return self._api_key

    def clear(self) -> None:
        """Invalidate this holder, including references copied to child contexts."""
        self._api_key = None


_REQUEST_CREDENTIAL: ContextVar[RequestCredential | None] = ContextVar(
    "dolibarr_request_credential",
    default=None,
)


def store_request_api_key(api_key: str) -> None:
    """Store a verified key in the current request's private holder."""
    credential = _REQUEST_CREDENTIAL.get()
    if credential is None:
        message = "Request credential context is unavailable"
        raise RuntimeError(message)
    credential.store(api_key)


def get_request_api_key() -> str:
    """Return the verified key for the current request only."""
    credential = _REQUEST_CREDENTIAL.get()
    if credential is None:
        message = "Request credential context is unavailable"
        raise PermissionError(message)
    return credential.get()


class RequestCredentialContextMiddleware:
    """Create and actively clear one credential holder around every ASGI request."""

    def __init__(self, app: ASGIApp) -> None:
        self._app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        credential = RequestCredential()
        context_token: Token[RequestCredential | None] = _REQUEST_CREDENTIAL.set(credential)
        try:
            await self._app(scope, receive, send)
        finally:
            credential.clear()
            _REQUEST_CREDENTIAL.reset(context_token)
