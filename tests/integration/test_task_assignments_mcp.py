"""Adding internal people to tasks through real HTTP auth and MCP."""

from __future__ import annotations

import json
import logging

import httpx2
import pytest
from mcp import Client
from mcp.client.streamable_http import streamable_http_client

from dolibarr_mcp.config import Settings
from tests.integration.test_http_mcp import IDENTITIES, asgi_client

pytestmark = [pytest.mark.anyio, pytest.mark.integration]


def relation(
    user_id: int = 8, *, source: str = "internal", code: str = "TASKCONTRIBUTOR", status: int = 4
) -> dict[str, object]:
    return {
        "id": user_id,
        "rowid": user_id + 100,
        "code": code,
        "source": source,
        "status": status,
        "email": "hidden",
        "address": "hidden",
    }


class AssignmentUpstream:
    """Record task-contact writes, distinguish link IDs, and preserve other assignments."""

    def __init__(self) -> None:
        self.task: dict[str, object] = {"id": 42, "fk_project": 10, "ref": "T42", "label": "Work"}
        self.project: dict[str, object] = {"id": 10, "ref": "P10", "title": "Delivery", "status": 1}
        self.user: dict[str, object] = {
            "id": 7,
            "login": "assignee",
            "status": 1,
            "email": "hidden",
            "password": "hidden",
        }
        self.contacts = [relation()]
        self.calls: list[httpx2.Request] = []
        self.writes: list[dict[str, object]] = []
        self.write_status = 200
        self.read_status = 200
        self.preflight_status = 200
        self.no_effect = False

    def handler(self, request: httpx2.Request) -> httpx2.Response:
        self.calls.append(request)
        assert request.headers["DOLAPIKEY"] in IDENTITIES
        assert not request.url.params
        path = request.url.path
        if path.endswith("/users/info"):
            return httpx2.Response(200, json=IDENTITIES[request.headers["DOLAPIKEY"]])
        if self.preflight_status != 200:
            return httpx2.Response(self.preflight_status, text="hidden upstream error")
        for suffix, payload in (
            ("/tasks/42", self.task),
            ("/projects/10", self.project),
            ("/users/7", self.user),
        ):
            if path.endswith(suffix):
                assert request.method == "GET"
                return httpx2.Response(200, json=payload)
        assert path.endswith("/tasks/42/contacts")
        if request.method == "GET":
            return httpx2.Response(self.read_status if self.writes else 200, json=self.contacts)
        assert request.method == "POST"
        payload = json.loads(request.content)
        self.writes.append(payload)
        if not self.no_effect:
            self.contacts.append(relation(payload["fk_socpeople"], code=payload["type_contact"]))
        return httpx2.Response(self.write_status, json={"private": "hidden"})


@pytest.mark.parametrize("token", ["fake-token-A", "fake-token-B"])
@pytest.mark.parametrize("role", ["TASKCONTRIBUTOR", "TASKEXECUTIVE"])
async def test_add_person_preserves_other_people_and_verifies_role(
    settings: Settings, token: str, role: str, caplog: pytest.LogCaptureFixture
) -> None:
    upstream = AssignmentUpstream()
    # An external contact may have exactly the same numeric ID as an internal user.
    upstream.contacts += [relation(7, source="external", code=role)]
    before = list(upstream.contacts)
    args: dict[str, object] = {"project_id": 10, "task_id": 42, "user_id": 7, "role": role}
    caplog.set_level(logging.DEBUG)
    async with (
        asgi_client(settings, httpx2.MockTransport(upstream.handler), token=token) as http,
        Client(
            streamable_http_client("http://localhost/mcp", http_client=http), mode="legacy"
        ) as client,
    ):
        tools = await client.list_tools()
        tool = next(tool for tool in tools.tools if tool.name == "dolibarr_task_assign")
        assert tool.annotations is not None
        assert tool.annotations.read_only_hint is False
        assert tool.annotations.destructive_hint is False
        assert (
            not {"api_key", "source", "notrigger", "type_contact", "sqlfilters"}
            & tool.input_schema["properties"].keys()
        )
        preview = await client.call_tool("dolibarr_task_assign", args)
        assert not preview.is_error
        assert preview.structured_content is not None
        assert preview.structured_content["target_kind"] == "task"
        assert preview.structured_content["target_id"] == 42
        assert len(preview.structured_content["changes"]) == 2
        assert upstream.writes == []
        # Upstream sorting may differ between calls and must not invalidate an unchanged preview.
        upstream.contacts.reverse()
        result = await client.call_tool(
            "dolibarr_task_assign",
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
    assignment = result.structured_content["task_assignment"]
    assert assignment["user"]["user_id"] == 7
    assert assignment["assigned"] is True
    assert assignment["role"] == role
    assert upstream.writes == [
        {"fk_socpeople": 7, "type_contact": role, "source": "internal", "notrigger": 0}
    ]
    assert all(contact in upstream.contacts for contact in before)
    assert "hidden" not in str(result)
    assert "hidden" not in caplog.text
    assert token not in caplog.text
    assert not any(request.method in {"PUT", "DELETE"} for request in upstream.calls)


async def test_existing_active_role_is_noop_and_default_role_is_contributor(
    settings: Settings,
) -> None:
    upstream = AssignmentUpstream()
    upstream.contacts += [relation(7)]
    args: dict[str, object] = {"project_id": 10, "task_id": 42, "user_id": 7}
    async with (
        asgi_client(settings, httpx2.MockTransport(upstream.handler), token="fake-token-A") as http,
        Client(
            streamable_http_client("http://localhost/mcp", http_client=http), mode="legacy"
        ) as client,
    ):
        preview = await client.call_tool("dolibarr_task_assign", args)
        assert preview.structured_content is not None
        assert preview.structured_content["changes"] == []
        assert preview.structured_content["warnings"]
        result = await client.call_tool(
            "dolibarr_task_assign",
            {
                **args,
                "apply": True,
                "confirmation_token": preview.structured_content["confirmation_token"],
            },
        )
    assert result.structured_content is not None
    assert result.structured_content["outcome"] == "no_op"
    assert result.structured_content["task_assignment"]["role"] == "TASKCONTRIBUTOR"
    assert upstream.writes == []


@pytest.mark.parametrize(
    "change",
    [
        "task_project",
        "task_id",
        "project_id",
        "project_closed",
        "inactive_user",
        "user_id",
        "user_name",
        "contacts",
        "role",
        "wrong_token",
        "missing_token",
    ],
)
async def test_changed_state_prevents_assignment(settings: Settings, change: str) -> None:
    upstream = AssignmentUpstream()
    args: dict[str, object] = {"project_id": 10, "task_id": 42, "user_id": 7}
    async with (
        asgi_client(settings, httpx2.MockTransport(upstream.handler), token="fake-token-A") as http,
        Client(
            streamable_http_client("http://localhost/mcp", http_client=http), mode="legacy"
        ) as client,
    ):
        preview = await client.call_tool("dolibarr_task_assign", args)
        assert preview.structured_content is not None
        token = preview.structured_content["confirmation_token"]
        if change == "task_project":
            upstream.task["fk_project"] = 99
        elif change == "task_id":
            upstream.task["id"] = 99
        elif change == "project_id":
            upstream.project["id"] = 99
        elif change == "project_closed":
            upstream.project["status"] = 2
        elif change == "inactive_user":
            upstream.user["status"] = 0
        elif change == "user_id":
            upstream.user["id"] = 99
        elif change == "user_name":
            upstream.user["login"] = "Changed"
        elif change == "contacts":
            upstream.contacts.append(relation(9))
        elif change == "role":
            args["role"] = "TASKEXECUTIVE"
        elif change == "wrong_token":
            token = "0" * 64
        else:
            token = None
        result = await client.call_tool(
            "dolibarr_task_assign", {**args, "apply": True, "confirmation_token": token}
        )
    assert result.is_error
    assert upstream.writes == []


@pytest.mark.parametrize(
    "case",
    ["no_effect", "read_denied", "write_failed", "ambiguous_write", "inactive_link", "other_role"],
)
async def test_assignment_partial_outcomes_are_verified_without_retry(
    settings: Settings, case: str
) -> None:
    upstream = AssignmentUpstream()
    if case == "no_effect":
        upstream.no_effect = True
    elif case == "read_denied":
        upstream.read_status = 403
    elif case == "write_failed":
        upstream.write_status = 403
        upstream.no_effect = True
    elif case == "ambiguous_write":
        upstream.write_status = 503
    elif case == "inactive_link":
        upstream.contacts.append(relation(7, status=5))
        upstream.no_effect = True
    else:
        upstream.contacts.append(relation(7, code="TASKEXECUTIVE"))
    args: dict[str, object] = {"project_id": 10, "task_id": 42, "user_id": 7}
    async with (
        asgi_client(settings, httpx2.MockTransport(upstream.handler), token="fake-token-A") as http,
        Client(
            streamable_http_client("http://localhost/mcp", http_client=http), mode="legacy"
        ) as client,
    ):
        preview = await client.call_tool("dolibarr_task_assign", args)
        assert preview.structured_content is not None
        assert preview.structured_content["changes"]
        result = await client.call_tool(
            "dolibarr_task_assign",
            {
                **args,
                "apply": True,
                "confirmation_token": preview.structured_content["confirmation_token"],
            },
        )
    assert not result.is_error
    assert result.structured_content is not None
    assert result.structured_content["outcome"] == (
        "applied" if case == "other_role" else "partial"
    )
    assert result.structured_content["target_kind"] == "task"
    assigned = result.structured_content["task_assignment"]["assigned"]
    assert assigned is (
        None if case == "read_denied" else case in {"ambiguous_write", "other_role"}
    )
    assert len(upstream.writes) == 1
    assert "hidden" not in str(result)


@pytest.mark.parametrize(
    "case", ["forbidden", "not_found", "too_many", "invalid_role", "invalid_id"]
)
async def test_invalid_or_unreadable_assignment_target_never_writes(
    settings: Settings, case: str
) -> None:
    upstream = AssignmentUpstream()
    args: dict[str, object] = {"project_id": 10, "task_id": 42, "user_id": 7}
    if case == "forbidden":
        upstream.preflight_status = 403
    elif case == "not_found":
        upstream.preflight_status = 404
    elif case == "too_many":
        upstream.contacts = [relation()] * 1001
    elif case == "invalid_role":
        args["role"] = "PROJECTLEADER"
    else:
        args["user_id"] = 0
    async with (
        asgi_client(settings, httpx2.MockTransport(upstream.handler), token="fake-token-A") as http,
        Client(
            streamable_http_client("http://localhost/mcp", http_client=http), mode="legacy"
        ) as client,
    ):
        result = await client.call_tool("dolibarr_task_assign", args)
    assert result.is_error
    assert "hidden" not in str(result)
    assert upstream.writes == []
