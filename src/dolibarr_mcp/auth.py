"""Strict HTTP Bearer authentication backed by Dolibarr on every request."""

from __future__ import annotations

import re
from http import HTTPStatus
from typing import TYPE_CHECKING, Final

from mcp.server.auth.middleware.bearer_auth import AuthenticatedUser, RequireAuthMiddleware
from mcp.server.auth.provider import AccessToken
from starlette.authentication import (
    AuthCredentials,
    AuthenticationBackend,
    AuthenticationError,
)
from starlette.responses import JSONResponse, Response

from dolibarr_mcp.credentials import store_request_api_key
from dolibarr_mcp.errors import DolibarrError

if TYPE_CHECKING:
    from starlette.requests import HTTPConnection
    from starlette.types import ASGIApp, Receive, Scope, Send

    from dolibarr_mcp.client import DolibarrClient

MAX_BEARER_TOKEN_LENGTH: Final = 4096
AUTHENTICATED_SCOPE: Final = "dolibarr:authenticated"
_TOKEN68 = re.compile(r"[A-Za-z0-9\-._~+/]+=*\Z")
_AUTH_REQUIRED = "Authentication required"
_CONTROL_CHARACTER_BOUNDARY = 32
_DELETE_CHARACTER = 127


def parse_bearer_token(headers: list[tuple[bytes, bytes]]) -> str:
    """Extract exactly one unmodified RFC token68 Bearer credential."""
    values = [value for name, value in headers if name.lower() == b"authorization"]
    if len(values) != 1:
        raise AuthenticationError(_AUTH_REQUIRED)
    try:
        header = values[0].decode("ascii")
    except UnicodeDecodeError:
        raise AuthenticationError(_AUTH_REQUIRED) from None
    scheme, separator, token = header.partition(" ")
    if separator != " " or scheme.casefold() != "bearer":
        raise AuthenticationError(_AUTH_REQUIRED)
    if not token or len(token) > MAX_BEARER_TOKEN_LENGTH:
        raise AuthenticationError(_AUTH_REQUIRED)
    if any(
        ord(character) < _CONTROL_CHARACTER_BOUNDARY or ord(character) == _DELETE_CHARACTER
        for character in token
    ):
        raise AuthenticationError(_AUTH_REQUIRED)
    if _TOKEN68.fullmatch(token) is None:
        raise AuthenticationError(_AUTH_REQUIRED)
    return token


class AuthenticationGatewayError(AuthenticationError):
    """Safe status metadata consumed by the synchronous Starlette error hook."""

    def __init__(self, error: DolibarrError) -> None:
        super().__init__(error.public_message)
        self.status_code = error.status_code
        self.retry_after = error.retry_after


class DolibarrAuthenticationBackend(AuthenticationBackend):
    """Authenticate only MCP paths, revalidating every request with Dolibarr."""

    def __init__(self, client: DolibarrClient) -> None:
        self._client = client

    async def authenticate(
        self, conn: HTTPConnection
    ) -> tuple[AuthCredentials, AuthenticatedUser] | None:
        if not _is_mcp_path(conn.scope):
            return None
        raw_headers = conn.scope.get("headers", [])
        token = parse_bearer_token(raw_headers)
        try:
            identity = await self._client.get_current_user(token)
        except DolibarrError as exc:
            raise AuthenticationGatewayError(exc) from None
        store_request_api_key(token)
        access_token = AccessToken(
            token="dolibarr-verified",  # noqa: S106 - deliberately not the presented credential
            client_id="dolibarr-user",
            scopes=[AUTHENTICATED_SCOPE],
            subject=str(identity.user_id),
            claims={"identity": identity.model_dump(mode="json")},
        )
        return AuthCredentials([AUTHENTICATED_SCOPE]), AuthenticatedUser(access_token)


def authentication_error_response(_request: HTTPConnection, error: AuthenticationError) -> Response:
    """Map auth failures without reflecting credentials or upstream details."""
    status_code = getattr(error, "status_code", HTTPStatus.UNAUTHORIZED)
    headers: dict[str, str] = {}
    if status_code == HTTPStatus.UNAUTHORIZED:
        headers["WWW-Authenticate"] = "Bearer"
    retry_after = getattr(error, "retry_after", None)
    if status_code == HTTPStatus.TOO_MANY_REQUESTS and retry_after is not None:
        headers["Retry-After"] = retry_after
    error_name = {
        401: "unauthorized",
        429: "rate_limited",
        502: "bad_gateway",
        503: "service_unavailable",
    }.get(status_code, "authentication_error")
    return JSONResponse({"error": error_name}, status_code=status_code, headers=headers)


class RequireMCPAuthMiddleware:
    """Apply the SDK's public scope-enforcement middleware only to MCP paths."""

    def __init__(self, app: ASGIApp) -> None:
        self._app = app
        self._required = RequireAuthMiddleware(
            app,
            required_scopes=[AUTHENTICATED_SCOPE],
            resource_metadata_url=None,
        )

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        target = self._required if _is_mcp_path(scope) else self._app
        await target(scope, receive, send)


def _is_mcp_path(scope: Scope) -> bool:
    path = str(scope.get("path", ""))
    return path == "/mcp" or path.startswith("/mcp/")
