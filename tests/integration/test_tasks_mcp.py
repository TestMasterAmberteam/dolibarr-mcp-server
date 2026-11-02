"""Task creation through HTTP authentication, MCP, and fixed upstream routes."""

from __future__ import annotations

import json
from datetime import date

import httpx2
import pytest
from mcp import Client
from mcp.client.streamable_http import streamable_http_client
from pydantic import ValidationError

from dolibarr_mcp.config import Settings
from dolibarr_mcp.models import TaskCreateInput
from tests.integration.test_http_mcp import IDENTITIES, asgi_client

pytestmark = [pytest.mark.anyio, pytest.mark.integration]


class TaskUpstream:
    """Stateful mock that records writes and can change between preview and apply."""

    def __init__(self, token: str = "fake-token-A") -> None:  # noqa: S107 - fictitious credential
        self.token = token
        self.project: dict[str, object] = {"id": 10, "ref": "P10", "title": "Delivery", "status": 1}
        self.parent: dict[str, object] = {"id": 5, "ref": "T5", "label": "Parent", "fk_project": 10}
        self.tasks: list[dict[str, object]] = []
        self.saved: dict[str, object] = {}
        self.calls: list[httpx2.Request] = []
        self.writes: list[dict[str, object]] = []
        self.read_status = 200
        self.write_status = 200
        self.created_id: object = 42
        self.overrides: dict[str, object] = {}

    def handler(self, request: httpx2.Request) -> httpx2.Response:
        self.calls.append(request)
        assert request.headers["DOLAPIKEY"] == self.token
        assert "sqlfilters" not in request.url.params
        path = request.url.path
        if path.endswith("/users/info"):
            return httpx2.Response(200, json=IDENTITIES[self.token])
        if request.method == "POST":
            assert path.endswith("/tasks")
            payload = json.loads(request.content)
            self.writes.append(payload)
            self.saved = {
                "id": 42,
                **payload,
                "ref": "T42",
                "note_private": "hidden",
                **self.overrides,
            }
            return httpx2.Response(self.write_status, json=self.created_id)
        assert request.method == "GET"
        if path.endswith("/projects/10"):
            return httpx2.Response(200, json=self.project)
        if path.endswith("/projects/10/tasks"):
            assert not request.url.params
            return httpx2.Response(200, json=self.tasks)
        if path.endswith("/tasks/5"):
            return httpx2.Response(200, json=self.parent)
        assert path.endswith("/tasks/42")
        return httpx2.Response(
            self.read_status, json=self.saved if self.read_status == 200 else "hidden"
        )


@pytest.mark.parametrize("token", ["fake-token-A", "fake-token-B"])
@pytest.mark.parametrize("with_optional_fields", [False, True])
async def test_task_preview_and_confirmed_create(
    settings: Settings, token: str, *, with_optional_fields: bool
) -> None:
    upstream = TaskUpstream(token)
    args: dict[str, object] = {"project_id": 10, "label": "  Implementation  "}
    if with_optional_fields:
        args.update(
            parent_task_id=5,
            description="Work",
            date_start="2026-11-02",
            date_end="2026-11-03",
            planned_workload_seconds=7200,
        )
    async with (
        asgi_client(settings, httpx2.MockTransport(upstream.handler), token=token) as http,
        Client(
            streamable_http_client("http://localhost/mcp", http_client=http), mode="legacy"
        ) as client,
    ):
        tools = await client.list_tools()
        tool = next(tool for tool in tools.tools if tool.name == "dolibarr_task_create")
        assert tool.annotations is not None
        assert tool.annotations.read_only_hint is False
        assert tool.annotations.idempotent_hint is False
        assert tool.annotations.destructive_hint is False
        assert (
            not {"api_key", "status", "ref", "sqlfilters", "progress", "entity"}
            & tool.input_schema["properties"].keys()
        )
        preview = await client.call_tool("dolibarr_task_create", args)
        assert not preview.is_error
        assert preview.structured_content is not None
        assert preview.structured_content["target_kind"] == "task"
        assert upstream.writes == []
        result = await client.call_tool(
            "dolibarr_task_create",
            {
                **args,
                "apply": True,
                "confirmation_token": preview.structured_content["confirmation_token"],
            },
        )
    assert not result.is_error
    assert result.structured_content is not None
    assert result.structured_content["outcome"] == "applied"
    assert result.structured_content["target_kind"] == "task"
    assert result.structured_content["task"]["task_id"] == 42
    assert result.structured_content["task"]["label"] == "Implementation"
    assert "hidden" not in str(result.structured_content)
    assert len(upstream.writes) == 1
    expected: dict[str, object] = {
        "ref": "auto",
        "fk_project": 10,
        "label": "Implementation",
        "fk_task_parent": 5 if with_optional_fields else 0,
    }
    if with_optional_fields:
        expected.update(
            description="Work", date_start=1793620800, date_end=1793707200, planned_workload=7200
        )
    assert upstream.writes == [expected]
    assert len([r for r in upstream.calls if r.url.path.endswith("/users/info")]) == 5


@pytest.mark.parametrize(
    "change", ["missing_token", "wrong_token", "project", "parent", "duplicates", "input"]
)
async def test_task_confirmation_rejects_missing_or_stale_state(
    settings: Settings, change: str
) -> None:
    upstream = TaskUpstream()
    args: dict[str, object] = {"project_id": 10, "label": "Implementation", "parent_task_id": 5}
    async with (
        asgi_client(settings, httpx2.MockTransport(upstream.handler), token=upstream.token) as http,
        Client(
            streamable_http_client("http://localhost/mcp", http_client=http), mode="legacy"
        ) as client,
    ):
        preview = await client.call_tool("dolibarr_task_create", args)
        assert preview.structured_content is not None
        token = preview.structured_content["confirmation_token"]
        if change == "project":
            upstream.project["title"] = "Changed"
        elif change == "parent":
            upstream.parent["label"] = "Changed"
        elif change == "duplicates":
            upstream.tasks = [{"id": 7, "ref": "T7", "label": "Implementation", "fk_project": 10}]
        elif change == "input":
            args["label"] = "Other"
        elif change == "wrong_token":
            token = "0" * 64
        else:
            token = None
        result = await client.call_tool(
            "dolibarr_task_create", {**args, "apply": True, "confirmation_token": token}
        )
    assert result.is_error
    assert upstream.writes == []


@pytest.mark.parametrize(
    "case", ["closed", "parent_project", "parent_id", "project_id", "too_many"]
)
async def test_task_invalid_target_prevents_write(settings: Settings, case: str) -> None:
    upstream = TaskUpstream()
    if case == "closed":
        upstream.project["status"] = 2
    elif case == "parent_project":
        upstream.parent["fk_project"] = 11
    elif case == "parent_id":
        upstream.parent["id"] = 99
    elif case == "project_id":
        upstream.project["id"] = 11
    else:
        upstream.tasks = [upstream.parent] * 1001
    async with (
        asgi_client(settings, httpx2.MockTransport(upstream.handler), token=upstream.token) as http,
        Client(
            streamable_http_client("http://localhost/mcp", http_client=http), mode="legacy"
        ) as client,
    ):
        result = await client.call_tool(
            "dolibarr_task_create", {"project_id": 10, "label": "Work", "parent_task_id": 5}
        )
    assert result.is_error
    assert upstream.writes == []


@pytest.mark.parametrize(
    "case", ["duplicate", "reread_denied", "mismatch", "timeout", "rejected", "bad_id"]
)
async def test_task_duplicate_and_write_outcomes(settings: Settings, case: str) -> None:
    upstream = TaskUpstream()
    upstream.tasks = [{"id": 5, "ref": "T5", "label": " WORK ", "fk_project": 10}]
    if case == "reread_denied":
        upstream.read_status = 403
    elif case == "mismatch":
        upstream.overrides["label"] = "Unexpected"
    elif case == "timeout":
        upstream.write_status = 503
    elif case == "rejected":
        upstream.write_status = 422
    elif case == "bad_id":
        upstream.created_id = 0
    args: dict[str, object] = {"project_id": 10, "label": "Work", "planned_workload_seconds": 0}
    async with (
        asgi_client(settings, httpx2.MockTransport(upstream.handler), token=upstream.token) as http,
        Client(
            streamable_http_client("http://localhost/mcp", http_client=http), mode="legacy"
        ) as client,
    ):
        preview = await client.call_tool("dolibarr_task_create", args)
        assert preview.structured_content is not None
        assert preview.structured_content["warnings"] == ["Possible duplicate task 5."]
        result = await client.call_tool(
            "dolibarr_task_create",
            {
                **args,
                "apply": True,
                "confirmation_token": preview.structured_content["confirmation_token"],
            },
        )
    assert len(upstream.writes) == 1
    assert "hidden" not in str(result)
    if case in {"timeout", "rejected", "bad_id"}:
        assert result.is_error
    else:
        assert not result.is_error
        assert result.structured_content is not None
        assert result.structured_content["target_kind"] == "task"
        assert result.structured_content["outcome"] == (
            "applied" if case == "duplicate" else "partial"
        )


@pytest.mark.parametrize(
    "fields",
    [
        {"label": "   "},
        {"project_id": 0},
        {"parent_task_id": -1},
        {"planned_workload_seconds": -1},
        {"planned_workload_seconds": 2_147_483_648},
        {"date_start": "2026-11-03", "date_end": "2026-11-02"},
        {"description": "x" * 4001},
        {"status": 1},
        {"api_key": "fake"},
    ],
)
def test_task_input_validation(fields: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        TaskCreateInput.model_validate({"project_id": 10, "label": "Work", **fields})


def test_task_optional_dates_allow_single_bound() -> None:
    assert (
        TaskCreateInput(project_id=10, label="Work", date_start=date(2026, 11, 2)).date_end is None
    )
