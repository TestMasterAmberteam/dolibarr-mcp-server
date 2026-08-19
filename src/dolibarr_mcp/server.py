"""MCP server definition containing exactly one read-only tool."""

from __future__ import annotations

from mcp.server import MCPServer
from mcp.server.auth.middleware.auth_context import get_access_token
from mcp.types import ToolAnnotations
from pydantic import ValidationError

from dolibarr_mcp.models import VerifiedIdentity


def create_mcp_server() -> MCPServer[None]:
    """Build the single shared MCP protocol server."""
    server: MCPServer[None] = MCPServer(
        name="dolibarr-mcp-server",
        description="Stateless, per-user access to Dolibarr ERP.",
        version="0.1.0",
    )

    @server.tool(
        name="dolibarr_whoami",
        description="Return the safe Dolibarr identity verified for this HTTP request.",
        annotations=ToolAnnotations(
            readOnlyHint=True,
            destructiveHint=False,
            idempotentHint=True,
            openWorldHint=False,
        ),
    )
    async def dolibarr_whoami() -> VerifiedIdentity:
        """Return the already verified identity without another Dolibarr request."""
        access_token = get_access_token()
        if access_token is None or access_token.claims is None:
            message = "Authentication context is unavailable"
            raise PermissionError(message)
        try:
            return VerifiedIdentity.model_validate(access_token.claims.get("identity"))
        except ValidationError:
            message = "Authentication context is invalid"
            raise PermissionError(message) from None

    return server
