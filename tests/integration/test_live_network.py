"""Full Streamable HTTP smoke test over a real loopback TCP socket."""

from __future__ import annotations

import asyncio
import socket

import httpx2
import pytest
import uvicorn
from mcp import Client
from mcp.client.streamable_http import streamable_http_client

from dolibarr_mcp.app import create_app
from dolibarr_mcp.config import Settings

pytestmark = [pytest.mark.anyio, pytest.mark.integration, pytest.mark.live_network]


async def test_uvicorn_initialize_list_call_and_clean_shutdown(settings: Settings) -> None:
    calls: list[str] = []

    async def dolibarr_handler(request: httpx2.Request) -> httpx2.Response:
        calls.append(request.headers["DOLAPIKEY"])
        return httpx2.Response(
            200,
            json={
                "id": 88,
                "login": "network-user",
                "firstname": "Network",
                "lastname": "User",
            },
        )

    app = create_app(settings, transport=httpx2.MockTransport(dolibarr_handler))
    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    listener.bind(("127.0.0.1", 0))
    listener.listen()
    port = int(listener.getsockname()[1])
    server = uvicorn.Server(
        uvicorn.Config(
            app,
            host="127.0.0.1",
            port=port,
            log_config=None,
            access_log=False,
            proxy_headers=False,
        )
    )
    server_task = asyncio.create_task(server.serve(sockets=[listener]))
    try:
        for _attempt in range(100):
            if server.started:
                break
            await asyncio.sleep(0.01)
        assert server.started
        async with httpx2.AsyncClient(
            headers={"Authorization": "Bearer fake-network-token"},
            trust_env=False,
        ) as http_client:
            transport = streamable_http_client(
                f"http://127.0.0.1:{port}/mcp",
                http_client=http_client,
            )
            async with Client(transport, mode="legacy") as mcp_client:
                tools = await mcp_client.list_tools()
                result = await mcp_client.call_tool("dolibarr_whoami", {})
        assert [tool.name for tool in tools.tools] == [
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
            "dolibarr_lead_stage_list",
            "dolibarr_lead_get",
            "dolibarr_lead_create",
            "dolibarr_lead_update",
            "dolibarr_lead_change_status",
            "dolibarr_lead_assign",
            "dolibarr_lead_open_project",
            "dolibarr_lead_close_project",
            "dolibarr_leave_type_list",
            "dolibarr_leave_request_search",
            "dolibarr_leave_request_get",
            "dolibarr_leave_request_create",
            "dolibarr_leave_request_update",
            "dolibarr_leave_request_submit",
            "dolibarr_leave_request_approve",
            "dolibarr_leave_request_refuse",
            "dolibarr_leave_request_cancel",
            "dolibarr_leave_request_reopen",
        ]
        assert result.structured_content == {
            "user_id": 88,
            "login": "network-user",
            "first_name": "Network",
            "last_name": "User",
        }
        assert calls == ["fake-network-token"] * 4
    finally:
        server.should_exit = True
        await asyncio.wait_for(server_task, timeout=5)
        listener.close()
