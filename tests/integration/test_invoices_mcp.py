"""Invoice tools through HTTP authentication and the real MCP protocol."""

from __future__ import annotations

import httpx2
import pytest
from mcp import Client
from mcp.client.streamable_http import streamable_http_client

from dolibarr_mcp.config import Settings
from tests.integration.test_http_mcp import IDENTITIES, asgi_client
from tests.invoice_fixtures import invoice_header, invoice_line

pytestmark = [pytest.mark.anyio, pytest.mark.integration]


@pytest.mark.parametrize("token", ["fake-token-A", "fake-token-B"])
async def test_invoice_tools_use_request_key_and_safe_public_schema(
    settings: Settings,
    token: str,
) -> None:
    seen: list[httpx2.Request] = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        seen.append(request)
        assert request.method == "GET"
        assert request.headers["DOLAPIKEY"] == token
        if request.url.path.endswith("/users/info"):
            return httpx2.Response(200, json=IDENTITIES[token])
        if request.url.path.endswith("/invoices"):
            return httpx2.Response(200, json=[invoice_header()])
        assert request.url.path.endswith("/invoices/1")
        return httpx2.Response(200, json=invoice_header(lines=[invoice_line(7), invoice_line(2)]))

    async with (
        asgi_client(settings, httpx2.MockTransport(handler), token=token) as client,
        Client(
            streamable_http_client("http://localhost/mcp", http_client=client), mode="legacy"
        ) as mcp_client,
    ):
        tools = await mcp_client.list_tools()
        for tool in tools.tools[-2:]:
            assert tool.annotations is not None
            assert tool.annotations.read_only_hint
            assert tool.annotations.idempotent_hint
            assert not tool.annotations.destructive_hint
            assert "api_key" not in str(tool.input_schema)
            assert "sqlfilters" not in str(tool.input_schema)
        search = await mcp_client.call_tool(
            "dolibarr_invoice_search", {"thirdparty_id": 42, "status": 1}
        )
        detail = await mcp_client.call_tool("dolibarr_invoice_get", {"invoice_id": 1, "limit": 1})
        rest = await mcp_client.call_tool("dolibarr_invoice_get", {"invoice_id": 1, "offset": 1})
    assert not search.is_error
    assert search.structured_content is not None
    assert search.structured_content["rows"][0]["invoice_id"] == 1
    assert search.structured_content["rows"][0]["total_net"] == "123.45"
    assert not detail.is_error
    assert detail.structured_content is not None
    assert detail.structured_content["header"]["thirdparty_id"] == 42
    assert detail.structured_content["lines"][0]["line_id"] == 7
    assert detail.structured_content["has_more"] is True
    assert rest.structured_content is not None
    assert rest.structured_content["lines"][0]["line_id"] == 2
    assert rest.structured_content["has_more"] is False
    assert "hidden" not in str(search.structured_content)
    assert "hidden" not in str(detail.structured_content)
    assert len([r for r in seen if r.url.path.endswith("/users/info")]) == 6
    assert len([r for r in seen if "/invoices" in r.url.path]) == 3


@pytest.mark.parametrize("status_code", [403, 404, 500])
async def test_invoice_tool_errors_do_not_expose_upstream_response(
    settings: Settings,
    status_code: int,
) -> None:
    def handler(request: httpx2.Request) -> httpx2.Response:
        if request.url.path.endswith("/users/info"):
            return httpx2.Response(200, json=IDENTITIES["fake-token-A"])
        return httpx2.Response(status_code, text="hidden invoice error")

    async with (
        asgi_client(settings, httpx2.MockTransport(handler), token="fake-token-A") as client,
        Client(
            streamable_http_client("http://localhost/mcp", http_client=client), mode="legacy"
        ) as mcp_client,
    ):
        result = await mcp_client.call_tool("dolibarr_invoice_get", {"invoice_id": 1})
    assert result.is_error
    assert "hidden" not in str(result)


@pytest.mark.parametrize(
    ("name", "arguments"),
    [
        ("dolibarr_invoice_get", {"invoice_id": 0}),
        ("dolibarr_invoice_get", {"invoice_id": 1, "offset": -1}),
        ("dolibarr_invoice_get", {"invoice_id": 1, "limit": 501}),
        ("dolibarr_invoice_search", {"thirdparty_id": -1}),
        ("dolibarr_invoice_search", {"status": 4}),
        ("dolibarr_invoice_search", {"query": "x" * 256}),
    ],
)
async def test_invalid_invoice_arguments_never_reach_invoice_endpoint(
    settings: Settings,
    name: str,
    arguments: dict[str, object],
) -> None:
    def handler(request: httpx2.Request) -> httpx2.Response:
        assert request.url.path.endswith("/users/info")
        return httpx2.Response(200, json=IDENTITIES["fake-token-A"])

    async with (
        asgi_client(settings, httpx2.MockTransport(handler), token="fake-token-A") as client,
        Client(
            streamable_http_client("http://localhost/mcp", http_client=client), mode="legacy"
        ) as mcp_client,
    ):
        result = await mcp_client.call_tool(name, arguments)
    assert result.is_error


async def test_invoice_call_without_auth_is_rejected(settings: Settings) -> None:
    def handler(_request: httpx2.Request) -> httpx2.Response:
        pytest.fail("Unauthenticated invoice call reached Dolibarr")

    async with asgi_client(settings, httpx2.MockTransport(handler)) as client:
        result = await client.post(
            "/mcp",
            json={
                "jsonrpc": "2.0",
                "id": 1,
                "method": "tools/call",
                "params": {"name": "dolibarr_invoice_get", "arguments": {"invoice_id": 1}},
            },
        )
    assert result.status_code == 401
