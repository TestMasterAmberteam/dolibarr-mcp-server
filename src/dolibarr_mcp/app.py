"""ASGI composition for transport security, authentication, MCP, and health."""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import TYPE_CHECKING

from mcp.server.auth.middleware.auth_context import AuthContextMiddleware
from mcp.server.transport_security import TransportSecuritySettings
from starlette.applications import Starlette
from starlette.middleware import Middleware
from starlette.middleware.authentication import AuthenticationMiddleware
from starlette.responses import JSONResponse, Response
from starlette.routing import Mount, Route

from dolibarr_mcp.auth import (
    DolibarrAuthenticationBackend,
    RequireMCPAuthMiddleware,
    authentication_error_response,
)
from dolibarr_mcp.client import DolibarrClient
from dolibarr_mcp.config import Settings
from dolibarr_mcp.logging import RequestContextMiddleware
from dolibarr_mcp.server import create_mcp_server

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Sequence

    import httpx2
    from starlette.requests import Request
    from starlette.types import ASGIApp, Receive, Scope, Send


class MCPTransportSecurityMiddleware:
    """Reject unsafe Host/Origin before any upstream authentication request."""

    def __init__(
        self, app: ASGIApp, *, allowed_hosts: list[str], allowed_origins: list[str]
    ) -> None:
        self._app = app
        self._allowed_hosts = allowed_hosts
        self._allowed_origins = allowed_origins

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if not _is_mcp_path(scope):
            await self._app(scope, receive, send)
            return
        host_values = _header_values(scope, b"host")
        if len(host_values) != 1 or not _allowed_value(host_values[0], self._allowed_hosts):
            await Response("Invalid Host header", status_code=421)(scope, receive, send)
            return
        origin_values = _header_values(scope, b"origin")
        if len(origin_values) > 1 or (
            origin_values and not _allowed_value(origin_values[0], self._allowed_origins)
        ):
            await Response("Invalid Origin header", status_code=403)(scope, receive, send)
            return
        await self._app(scope, receive, send)


def _header_values(scope: Scope, name: bytes) -> list[str]:
    values: list[str] = []
    for header_name, value in scope.get("headers", []):
        if header_name.lower() == name:
            try:
                values.append(value.decode("ascii"))
            except UnicodeDecodeError:
                return []
    return values


def _allowed_value(value: str, allowed: Sequence[str]) -> bool:
    if value in allowed:
        return True
    return any(entry.endswith(":*") and value.startswith(f"{entry[:-2]}:") for entry in allowed)


def _is_mcp_path(scope: Scope) -> bool:
    path = str(scope.get("path", ""))
    return path == "/mcp" or path.startswith("/mcp/")


def create_app(
    settings: Settings,
    *,
    transport: httpx2.AsyncBaseTransport | None = None,
) -> Starlette:
    """Compose one production ASGI application and its shared resources."""
    client = DolibarrClient(settings, transport=transport)
    mcp_server = create_mcp_server()
    transport_security = TransportSecuritySettings(
        enable_dns_rebinding_protection=True,
        allowed_hosts=settings.allowed_hosts,
        allowed_origins=settings.allowed_origins,
    )
    mcp_app = mcp_server.streamable_http_app(
        streamable_http_path="/mcp",
        stateless_http=True,
        json_response=True,
        transport_security=transport_security,
        host=settings.host,
    )

    @asynccontextmanager
    async def lifespan(app: Starlette) -> AsyncIterator[None]:
        app.state.ready = False
        async with client, mcp_server.session_manager.run():
            app.state.ready = True
            try:
                yield
            finally:
                app.state.ready = False

    async def live(_request: Request) -> JSONResponse:
        return JSONResponse({"status": "ok"})

    async def ready(request: Request) -> JSONResponse:
        is_ready = bool(getattr(request.app.state, "ready", False))
        return JSONResponse(
            {"status": "ok" if is_ready else "not_ready"},
            status_code=200 if is_ready else 503,
        )

    middleware = [
        Middleware(RequestContextMiddleware),
        Middleware(
            MCPTransportSecurityMiddleware,
            allowed_hosts=settings.allowed_hosts,
            allowed_origins=settings.allowed_origins,
        ),
        Middleware(
            AuthenticationMiddleware,
            backend=DolibarrAuthenticationBackend(client),
            on_error=authentication_error_response,
        ),
        Middleware(AuthContextMiddleware),
        Middleware(RequireMCPAuthMiddleware),
    ]
    app = Starlette(
        routes=[
            Route("/health/live", live, methods=["GET"]),
            Route("/health/ready", ready, methods=["GET"]),
            Mount("/", app=mcp_app),
        ],
        middleware=middleware,
        lifespan=lifespan,
    )
    app.state.ready = False
    app.state.dolibarr_client = client
    app.state.mcp_server = mcp_server
    return app


def build_app_from_environment() -> Starlette:
    """Validate environment configuration and return the ASGI app."""
    return create_app(Settings())
