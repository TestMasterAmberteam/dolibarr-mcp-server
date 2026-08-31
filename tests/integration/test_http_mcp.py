"""Integration tests through the real ASGI auth and MCP transport stack."""

from __future__ import annotations

import asyncio
import logging
from collections import Counter
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime

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

TOOL_NAMES = [
    "dolibarr_whoami",
    "dolibarr_my_time_report",
    "dolibarr_project_time_report",
    "dolibarr_task_timespent",
    "dolibarr_time_summary",
    "dolibarr_time_entries",
    "dolibarr_thirdparty_search",
    "dolibarr_thirdparty_get",
    "dolibarr_thirdparty_create",
    "dolibarr_thirdparty_update",
    "dolibarr_user_search",
    "dolibarr_lead_search",
    "dolibarr_lead_get",
    "dolibarr_lead_create",
    "dolibarr_lead_update",
    "dolibarr_lead_change_status",
    "dolibarr_lead_assign",
    "dolibarr_lead_open_project",
]

READ_ONLY_TOOL_NAMES = {
    *TOOL_NAMES[:8],
    "dolibarr_user_search",
    "dolibarr_lead_search",
    "dolibarr_lead_get",
}
CREATE_TOOL_NAMES = {"dolibarr_thirdparty_create", "dolibarr_lead_create"}
MUTATION_TOOL_NAMES = set(TOOL_NAMES) - READ_ONLY_TOOL_NAMES - CREATE_TOOL_NAMES


def report_line(
    entry_id: int,
    *,
    task_id: int,
    user_id: int,
    day: int,
    duration: int,
) -> dict[str, object]:
    timestamp = int(datetime(2026, 8, day, 12, tzinfo=UTC).timestamp())
    return {
        "timespent_line_id": str(entry_id),
        "timespent_line_date": timestamp,
        "timespent_line_datehour": timestamp,
        "timespent_line_duration": str(duration),
        "timespent_line_fk_user": str(user_id),
        "timespent_line_note": f"entry {entry_id}",
        "fk_project": "10",
        "project_ref": "P-10",
        "project_label": "Project Ten",
        "fk_task": str(task_id),
        "task_ref": f"T-{task_id}",
        "task_label": f"Task {task_id}",
    }


def report_task(task_id: int, lines: list[dict[str, object]] | None) -> dict[str, object]:
    return {
        "id": str(task_id),
        "fk_project": "10",
        "ref": f"T-{task_id}",
        "label": f"Task {task_id}",
        "lines": lines,
    }


def reporting_transport(calls: list[tuple[str, str]]) -> httpx2.MockTransport:
    task_lines = {
        100: [report_line(1, task_id=100, user_id=1, day=1, duration=3600)],
        101: [report_line(2, task_id=101, user_id=2, day=8, duration=1800)],
    }

    async def handler(request: httpx2.Request) -> httpx2.Response:
        token = request.headers.get("DOLAPIKEY", "")
        path = request.url.path
        calls.append((path, token))
        if path.endswith("/users/info"):
            response = httpx2.Response(200, json=IDENTITIES[token])
        elif path.endswith("/projects/10/tasks"):
            response = httpx2.Response(
                200,
                json=[report_task(task_id, lines) for task_id, lines in task_lines.items()],
            )
        elif path.endswith("/projects/10"):
            response = httpx2.Response(
                200,
                json={"id": "10", "ref": "P-10", "title": "Project Ten"},
            )
        elif path.endswith("/tasks/100/timespent"):
            response = httpx2.Response(200, json=task_lines[100])
        elif path.endswith("/tasks/101/timespent"):
            response = httpx2.Response(200, json=task_lines[101])
        elif path.endswith("/tasks/100"):
            response = httpx2.Response(200, json=report_task(100, None))
        elif path.endswith("/tasks"):
            response = httpx2.Response(
                200,
                json=[report_task(task_id, None) for task_id in task_lines],
            )
        elif path.endswith("/users"):
            response = httpx2.Response(200, json=list(IDENTITIES.values()))
        else:
            raise AssertionError(path)
        return response

    return httpx2.MockTransport(handler)


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
    assert tools == TOOL_NAMES
    assert identity == {
        "user_id": 1,
        "login": "alice",
        "first_name": "Alice",
        "last_name": "Able",
    }
    # initialize, tools/list, tools/call, and session DELETE are each authenticated once.
    assert calls == ["fake-token-A"] * 4


async def test_all_reporting_tools_cross_real_auth_and_mcp_stack(settings: Settings) -> None:
    calls: list[tuple[str, str]] = []
    async with asgi_client(
        settings,
        reporting_transport(calls),
        token="fake-token-A",
    ) as client:
        transport = streamable_http_client("http://localhost/mcp", http_client=client)
        async with Client(transport, mode="legacy") as mcp_client:
            tools = await mcp_client.list_tools()
            my_report = await mcp_client.call_tool(
                "dolibarr_my_time_report",
                {"date_from": "2026-08-01", "date_to": "2026-08-31"},
            )
            project_report = await mcp_client.call_tool(
                "dolibarr_project_time_report",
                {
                    "project_id": 10,
                    "date_from": "2026-08-01",
                    "date_to": "2026-08-31",
                },
            )
            task_report = await mcp_client.call_tool(
                "dolibarr_task_timespent",
                {
                    "task_id": 100,
                    "date_from": "2026-08-01",
                    "date_to": "2026-08-31",
                },
            )
            summary = await mcp_client.call_tool(
                "dolibarr_time_summary",
                {
                    "date_from": "2026-08-01",
                    "date_to": "2026-08-31",
                    "group_by": "user",
                    "project_id": 10,
                },
            )
            entries = await mcp_client.call_tool(
                "dolibarr_time_entries",
                {
                    "date_from": "2026-08-01",
                    "date_to": "2026-08-31",
                    "task_id": 100,
                },
            )
    assert [tool.name for tool in tools.tools] == TOOL_NAMES
    tool_by_name = {tool.name: tool for tool in tools.tools}
    annotations_by_name = {}
    for name, tool in tool_by_name.items():
        assert tool.annotations is not None, name
        annotations_by_name[name] = tool.annotations
    assert all(annotations_by_name[name].read_only_hint for name in READ_ONLY_TOOL_NAMES)
    assert all(
        not annotations_by_name[name].read_only_hint
        and not annotations_by_name[name].destructive_hint
        for name in CREATE_TOOL_NAMES
    )
    assert all(
        not annotations_by_name[name].read_only_hint and annotations_by_name[name].destructive_hint
        for name in MUTATION_TOOL_NAMES
    )
    assert my_report.structured_content is not None
    assert my_report.structured_content["duration_seconds"] == 3600
    assert project_report.structured_content is not None
    assert project_report.structured_content["duration_seconds"] == 5400
    assert task_report.structured_content is not None
    assert task_report.structured_content["entries"][0]["note"] == "entry 1"
    assert summary.structured_content is not None
    assert summary.structured_content["group_count"] == 2
    assert entries.structured_content is not None
    assert entries.structured_content["entry_count"] == 1
    assert all(token == "fake-token-A" for _path, token in calls)
    assert all("api_key" not in str(tool.input_schema).lower() for tool in tools.tools)
    assert all("sqlfilters" not in str(tool.input_schema).lower() for tool in tools.tools)
    assert all("upstream" not in str(tool.input_schema).lower() for tool in tools.tools)


async def test_confirmed_sales_write_crosses_auth_and_uses_request_key(
    settings: Settings,
) -> None:
    calls: list[tuple[str, str, str]] = []
    created = False

    async def handler(request: httpx2.Request) -> httpx2.Response:
        nonlocal created
        path = request.url.path
        token = request.headers.get("DOLAPIKEY", "")
        calls.append((request.method, path, token))
        if path.endswith("/users/info"):
            return httpx2.Response(200, json=IDENTITIES[token])
        if path.endswith("/thirdparties") and request.method == "GET":
            return httpx2.Response(200, json=[])
        if path.endswith("/thirdparties") and request.method == "POST":
            created = True
            assert request.content == b'{"name":"New Prospect","client":2}'
            return httpx2.Response(200, json=3)
        if path.endswith("/thirdparties/3") and created:
            return httpx2.Response(
                200,
                json={"id": 3, "name": "New Prospect", "client": 2},
            )
        raise AssertionError((request.method, path))

    async with asgi_client(
        settings,
        httpx2.MockTransport(handler),
        token="fake-token-A",
    ) as client:
        transport = streamable_http_client("http://localhost/mcp", http_client=client)
        async with Client(transport, mode="legacy") as mcp_client:
            preview = await mcp_client.call_tool(
                "dolibarr_thirdparty_create",
                {"name": "New Prospect", "customer_status": "prospect"},
            )
            assert preview.structured_content is not None
            assert preview.structured_content["apply"] is False
            confirmation_token = preview.structured_content["confirmation_token"]

            rejected = await mcp_client.call_tool(
                "dolibarr_thirdparty_create",
                {
                    "name": "New Prospect",
                    "customer_status": "prospect",
                    "apply": True,
                    "confirmation_token": "0" * 64,
                },
            )
            assert rejected.is_error is True
            assert created is False

            applied = await mcp_client.call_tool(
                "dolibarr_thirdparty_create",
                {
                    "name": "New Prospect",
                    "customer_status": "prospect",
                    "apply": True,
                    "confirmation_token": confirmation_token,
                },
            )
            assert applied.structured_content is not None
            assert applied.structured_content["outcome"] == "applied"
            assert applied.structured_content["thirdparty"]["customer_status"] == "prospect"

    write_calls = [
        call for call in calls if call[0] == "POST" and call[1].endswith("/thirdparties")
    ]
    assert write_calls == [("POST", "/dolibarr/api/index.php/thirdparties", "fake-token-A")]
    assert all(token == "fake-token-A" for _method, _path, token in calls)


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
