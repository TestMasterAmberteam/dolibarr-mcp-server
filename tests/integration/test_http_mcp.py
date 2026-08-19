"""Integration tests through the real ASGI auth and MCP transport stack."""

from __future__ import annotations

import asyncio
import logging
from collections import Counter
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import httpx2
import pytest
from asgi_lifespan import LifespanManager
from mcp import Client
from mcp.client.streamable_http import streamable_http_client

from dolibarr_mcp.app import create_app
from dolibarr_mcp.config import Settings

pytestmark = [pytest.mark.anyio, pytest.mark.integration]

IDENTITIES = {
    "fake-token-A": {"id": 1, "login": "alice", "firstname": "Alice", "lastname": "Able"},
    "fake-token-B": {"id": 2, "login": "bob", "firstname": "Bob", "lastname": "Baker"},
}


def dolibarr_transport(
    *,
    status_by_token: dict[str, int] | None = None,
    calls: list[str] | None = None,
    retry_after: str | None = None,
) -> httpx2.MockTransport:
    async def handler(request: httpx2.Request) -> httpx2.Response:
        token = request.headers.get("DOLAPIKEY", "")
        if calls is not None:
            calls.append(token)
        status = (status_by_token or {}).get(token, 200)
        if status != 200:
            headers = {"Retry-After": retry_after} if retry_after is not None else {}
            return httpx2.Response(status, headers=headers)
        return httpx2.Response(200, json=IDENTITIES[token])

    return httpx2.MockTransport(handler)


@asynccontextmanager
async def asgi_client(
    settings: Settings,
    transport: httpx2.AsyncBaseTransport,
    *,
    token: str | None = None,
) -> AsyncIterator[httpx2.AsyncClient]:
    app = create_app(settings, transport=transport)
    headers = {"Authorization": f"Bearer {token}"} if token is not None else {}
    async with (
        LifespanManager(app),
        httpx2.AsyncClient(
            transport=httpx2.ASGITransport(app=app),
            base_url="http://localhost",
            headers=headers,
        ) as client,
    ):
        yield client


async def run_mcp_flow(client: httpx2.AsyncClient) -> tuple[list[str], dict[str, object]]:
    transport = streamable_http_client("http://localhost/mcp", http_client=client)
    async with Client(transport, mode="legacy") as mcp_client:
        tools = await mcp_client.list_tools()
        result = await mcp_client.call_tool("dolibarr_whoami", {})
    structured = result.structured_content
    assert structured is not None
    return [tool.name for tool in tools.tools], structured


async def test_valid_token_initialize_list_and_call(settings: Settings) -> None:
    calls: list[str] = []
    async with asgi_client(
        settings, dolibarr_transport(calls=calls), token="fake-token-A"
    ) as client:
        tools, identity = await run_mcp_flow(client)
    assert tools == ["dolibarr_whoami"]
    assert identity == {
        "user_id": 1,
        "login": "alice",
        "first_name": "Alice",
        "last_name": "Able",
    }
    # initialize, tools/list, tools/call, and session DELETE are each authenticated once.
    assert calls == ["fake-token-A"] * 4


@pytest.mark.parametrize(
    ("authorization", "status_code"),
    [(None, 401), ("Basic fake", 401), ("Bearer ", 401), ("Bearer bad token", 401)],
)
async def test_missing_and_malformed_auth_stop_before_mcp(
    settings: Settings, authorization: str | None, status_code: int
) -> None:
    calls: list[str] = []
    async with asgi_client(settings, dolibarr_transport(calls=calls)) as client:
        headers = {"Authorization": authorization} if authorization is not None else {}
        response = await client.post(
            "/mcp",
            headers=headers,
            json={"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}},
        )
    assert response.status_code == status_code
    assert response.headers["WWW-Authenticate"] == "Bearer"
    assert calls == []


@pytest.mark.parametrize(
    ("upstream_status", "expected_status"),
    [(401, 401), (403, 401), (500, 503), (503, 503), (404, 502)],
)
async def test_upstream_errors_are_safely_mapped(
    settings: Settings, upstream_status: int, expected_status: int
) -> None:
    token = "fake-token-A"
    transport = dolibarr_transport(status_by_token={token: upstream_status})
    async with asgi_client(settings, transport, token=token) as client:
        response = await client.post("/mcp", json={"not": "processed"})
    assert response.status_code == expected_status
    assert token not in response.text
    if expected_status == 401:
        assert response.headers["WWW-Authenticate"] == "Bearer"


async def test_rate_limit_and_validated_retry_after(settings: Settings) -> None:
    token = "fake-token-A"
    transport = dolibarr_transport(
        status_by_token={token: 429},
        retry_after="30",
    )
    async with asgi_client(settings, transport, token=token) as client:
        response = await client.post("/mcp", json={"not": "processed"})
    assert response.status_code == 429
    assert response.headers["Retry-After"] == "30"
    assert token not in response.text


async def test_concurrent_users_never_mix_identity(settings: Settings) -> None:
    calls: list[str] = []
    app = create_app(settings, transport=dolibarr_transport(calls=calls))

    async def flow(token: str) -> dict[str, object]:
        async with httpx2.AsyncClient(
            transport=httpx2.ASGITransport(app=app),
            base_url="http://localhost",
            headers={"Authorization": f"Bearer {token}"},
        ) as client:
            _, identity = await run_mcp_flow(client)
            return identity

    async with LifespanManager(app):
        identity_a, identity_b = await asyncio.gather(flow("fake-token-A"), flow("fake-token-B"))
    assert identity_a["login"] == "alice"
    assert identity_b["login"] == "bob"
    assert Counter(calls) == Counter({"fake-token-A": 4, "fake-token-B": 4})


async def test_repeated_requests_revalidate_without_cache(settings: Settings) -> None:
    calls: list[str] = []
    async with asgi_client(
        settings, dolibarr_transport(calls=calls), token="fake-token-A"
    ) as client:
        await client.post("/mcp", json={"not": "mcp"})
        await client.post("/mcp", json={"not": "mcp"})
    assert calls == ["fake-token-A", "fake-token-A"]


async def test_health_endpoints_are_public_and_do_not_call_dolibarr(settings: Settings) -> None:
    calls: list[str] = []
    async with asgi_client(settings, dolibarr_transport(calls=calls)) as client:
        live = await client.get("/health/live")
        ready = await client.get("/health/ready")
    assert live.status_code == 200
    assert live.json() == {"status": "ok"}
    assert ready.status_code == 200
    assert ready.json() == {"status": "ok"}
    assert calls == []


async def test_disallowed_host_is_rejected_before_authentication(settings: Settings) -> None:
    calls: list[str] = []
    app = create_app(settings, transport=dolibarr_transport(calls=calls))
    async with (
        LifespanManager(app),
        httpx2.AsyncClient(
            transport=httpx2.ASGITransport(app=app), base_url="http://evil.example"
        ) as client,
    ):
        response = await client.post(
            "/mcp",
            headers={"Authorization": "Bearer fake-token-A"},
            json={"not": "processed"},
        )
    assert response.status_code == 421
    assert calls == []


async def test_disallowed_origin_is_rejected_before_authentication(settings: Settings) -> None:
    calls: list[str] = []
    async with asgi_client(settings, dolibarr_transport(calls=calls)) as client:
        response = await client.post(
            "/mcp",
            headers={
                "Authorization": "Bearer fake-token-A",
                "Origin": "https://evil.example",
            },
            json={"not": "processed"},
        )
    assert response.status_code == 403
    assert calls == []


async def test_readiness_is_false_before_lifespan_start(settings: Settings) -> None:
    app = create_app(settings, transport=dolibarr_transport())
    async with httpx2.AsyncClient(
        transport=httpx2.ASGITransport(app=app), base_url="http://localhost"
    ) as client:
        response = await client.get("/health/ready")
    assert response.status_code == 503
    assert response.json() == {"status": "not_ready"}


async def test_token_never_appears_in_logs_or_error_response(
    settings: Settings, caplog: pytest.LogCaptureFixture
) -> None:
    token = "fake-token-that-must-never-leak"
    caplog.set_level(logging.DEBUG)
    transport = dolibarr_transport(status_by_token={token: 500})
    async with asgi_client(settings, transport, token=token) as client:
        response = await client.post("/mcp", json={"not": "processed"})
    assert token not in response.text
    assert all(token not in record.getMessage() for record in caplog.records)
