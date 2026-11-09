"""Daily time writes through HTTP authentication, MCP, and mocked official REST routes."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from decimal import Decimal

import httpx2
import pytest
from mcp import Client
from mcp.client.streamable_http import streamable_http_client
from pydantic import ValidationError

from dolibarr_mcp.config import Settings
from dolibarr_mcp.models import TimeEntryCreateInput
from tests.integration.test_http_mcp import IDENTITIES, asgi_client

pytestmark = [pytest.mark.anyio, pytest.mark.integration]

ARGS: dict[str, object] = {
    "work_date": "2026-11-09",
    "task_id": 5,
    "hours": "2.5",
    "comment": "Implementation and review",
}


def entry(entry_id: int = 42, user_id: int = 1, **overrides: object) -> dict[str, object]:
    return {
        "timespent_line_id": entry_id,
        "timespent_line_date": int(datetime(2026, 11, 9, tzinfo=UTC).timestamp()),
        "timespent_line_fk_user": user_id,
        "timespent_line_duration": 9000,
        "timespent_line_note": ARGS["comment"],
        "fk_task": 5,
        "fk_project": 10,
        "private_data": "hidden",
        **overrides,
    }


class TimeUpstream:
    """Record exact writes and simulate ambiguous outcomes without live records."""

    def __init__(self, token: str = "fake-token-A") -> None:  # noqa: S107 - fictitious key
        self.token = token
        self.task: dict[str, object] = {"id": 5, "ref": "T5", "label": "Work", "fk_project": 10}
        self.project: dict[str, object] = {"id": 10, "ref": "P10", "title": "Work", "status": 1}
        self.lines: list[dict[str, object]] = []
        self.writes: list[dict[str, object]] = []
        self.calls: list[httpx2.Request] = []
        self.mode = "ok"
        self.read_status = 200

    def handler(self, request: httpx2.Request) -> httpx2.Response:  # noqa: PLR0911, PLR0912 - explicit mock failures
        self.calls.append(request)
        assert request.headers["DOLAPIKEY"] == self.token
        assert not request.url.params
        path = request.url.path
        if path.endswith("/users/info"):
            return httpx2.Response(200, json=IDENTITIES[self.token])
        if request.method == "POST":
            assert path.endswith("/tasks/5/addtimespent")
            payload = json.loads(request.content)
            self.writes.append(payload)
            if self.mode not in {"missing", "rejected", "timeout", "reread"}:
                line = entry(
                    42,
                    1 if self.token == "fake-token-A" else 2,
                    timespent_line_duration=payload["duration"],
                    timespent_line_note=payload["note"],
                )
                if self.mode == "mismatch":
                    line["timespent_line_duration"] = 1
                if self.mode == "wrong_day":
                    line["timespent_line_date"] = 0
                if self.mode == "wrong_user":
                    line["timespent_line_fk_user"] = 99
                if self.mode == "wrong_task":
                    line["fk_task"] = 99
                self.lines.append(line)
                if self.mode == "ambiguous":
                    self.lines.append({**line, "timespent_line_id": 43})
            if self.mode in {"timeout", "timeout_saved"}:
                msg = "hidden"
                raise httpx2.ReadTimeout(msg, request=request)
            if self.mode == "rejected":
                return httpx2.Response(403, json="hidden")
            if self.mode == "bad_json":
                return httpx2.Response(200, text="hidden")
            return httpx2.Response(200, json={"success": {"code": 200, "message": "hidden"}})
        assert request.method == "GET"
        if path.endswith("/tasks/5"):
            return httpx2.Response(self.read_status, json=self.task)
        if path.endswith("/projects/10"):
            return httpx2.Response(200, json=self.project)
        assert path.endswith("/tasks/5/timespent")
        if self.writes and self.mode == "reread":
            return httpx2.Response(503, json="hidden")
        return httpx2.Response(200, json=self.lines)


@pytest.mark.parametrize("token", ["fake-token-A", "fake-token-B"])
@pytest.mark.parametrize(("hours", "seconds"), [("2.5", 9000), ("0.1", 360), ("24", 86400)])
async def test_daily_preview_apply_and_replay(
    settings: Settings, token: str, hours: str, seconds: int
) -> None:
    upstream = TimeUpstream(token)
    args = {**ARGS, "hours": hours}
    upstream.lines = [entry(1, 99, timespent_line_note="other user's hidden comment")]
    async with (
        asgi_client(settings, httpx2.MockTransport(upstream.handler), token=token) as http,
        Client(
            streamable_http_client("http://localhost/mcp", http_client=http), mode="legacy"
        ) as c,
    ):
        tools = await c.list_tools()
        tool = next(t for t in tools.tools if t.name == "dolibarr_time_entry_create")
        assert tool.annotations is not None
        assert tool.annotations.read_only_hint is False
        assert tool.annotations.idempotent_hint is False
        assert tool.annotations.destructive_hint is False
        assert set(tool.input_schema["properties"]) == {
            "work_date",
            "task_id",
            "hours",
            "comment",
            "apply",
            "confirmation_token",
        }
        preview = await c.call_tool(tool.name, args)
        assert not preview.is_error
        assert preview.structured_content is not None
        assert preview.structured_content["target_kind"] == "task"
        changes = {row["field"]: row["after"] for row in preview.structured_content["changes"]}
        assert changes["hours"] == hours
        assert changes["duration_seconds"] == seconds
        assert upstream.writes == []
        confirmed = {
            **args,
            "apply": True,
            "confirmation_token": preview.structured_content["confirmation_token"],
        }
        result = await c.call_tool(tool.name, confirmed)
        replay = await c.call_tool(tool.name, confirmed)
    assert not result.is_error
    assert replay.is_error
    assert result.structured_content is not None
    detail = result.structured_content["time_entry"]
    assert result.structured_content["outcome"] == "applied"
    assert result.structured_content["target_id"] == 5
    assert detail == {
        "entry_id": 42,
        "task_id": 5,
        "project_id": 10,
        "user_id": IDENTITIES[token]["id"],
        "work_date": ARGS["work_date"],
        "duration_seconds": seconds,
        "hours": hours,
        "comment": ARGS["comment"],
    }
    assert "hidden" not in str(result)
    assert "hidden" not in str(preview)
    assert upstream.writes == [
        {
            "date": "2026-11-09 00:00:00",
            "duration": seconds,
            "user_id": 0,
            "note": ARGS["comment"],
            "progress": -1,
        }
    ]
    # initialize, list, preview, apply, replay, DELETE: no second identity lookup per request.
    assert sum(r.url.path.endswith("/users/info") for r in upstream.calls) == 6


@pytest.mark.parametrize(
    "change", ["missing", "wrong", "input", "task", "project", "entries", "user"]
)
async def test_daily_stale_preview(settings: Settings, change: str) -> None:
    upstream = TimeUpstream()
    async with (
        asgi_client(settings, httpx2.MockTransport(upstream.handler), token=upstream.token) as http,
        Client(
            streamable_http_client("http://localhost/mcp", http_client=http), mode="legacy"
        ) as c,
    ):
        preview = await c.call_tool("dolibarr_time_entry_create", ARGS)
        assert preview.structured_content is not None
        token = preview.structured_content["confirmation_token"]
        args = dict(ARGS)
        if change == "missing":
            token = None
        elif change == "wrong":
            token = "0" * 64
        elif change == "input":
            args["hours"] = "3"
        elif change == "task":
            upstream.task["label"] = "Changed"
        elif change == "project":
            upstream.project["title"] = "Changed"
        elif change == "entries":
            upstream.lines.append(entry(3))
        else:
            upstream.token = "fake-token-B"
            http.headers["Authorization"] = "Bearer fake-token-B"
        result = await c.call_tool(
            "dolibarr_time_entry_create", {**args, "apply": True, "confirmation_token": token}
        )
    assert result.is_error
    assert upstream.writes == []


@pytest.mark.parametrize(
    "case",
    [
        "closed",
        "task_id",
        "project_id",
        "forbidden",
        "missing",
        "cross_task",
        "cross_project",
        "repeated",
    ],
)
async def test_daily_rejects_unauthorized_or_inconsistent_reads(
    settings: Settings, case: str
) -> None:
    upstream = TimeUpstream()
    if case == "closed":
        upstream.project["status"] = 2
    elif case == "task_id":
        upstream.task["id"] = 99
    elif case == "project_id":
        upstream.project["id"] = 99
    elif case in {"forbidden", "missing"}:
        upstream.read_status = 403 if case == "forbidden" else 404
    elif case == "cross_task":
        upstream.lines = [entry(fk_task=99)]
    elif case == "cross_project":
        upstream.lines = [entry(fk_project=99)]
    else:
        upstream.lines = [entry(), entry()]
    async with (
        asgi_client(settings, httpx2.MockTransport(upstream.handler), token=upstream.token) as http,
        Client(
            streamable_http_client("http://localhost/mcp", http_client=http), mode="legacy"
        ) as c,
    ):
        result = await c.call_tool("dolibarr_time_entry_create", ARGS)
    assert result.is_error
    assert upstream.writes == []
    assert "hidden" not in str(result)


@pytest.mark.parametrize(
    "case",
    [
        "missing",
        "mismatch",
        "ambiguous",
        "timeout",
        "timeout_saved",
        "rejected",
        "reread",
        "bad_json",
        "wrong_day",
        "wrong_user",
        "wrong_task",
    ],
)
async def test_daily_partial_write_outcomes(settings: Settings, case: str) -> None:
    upstream = TimeUpstream()
    async with (
        asgi_client(settings, httpx2.MockTransport(upstream.handler), token=upstream.token) as http,
        Client(
            streamable_http_client("http://localhost/mcp", http_client=http), mode="legacy"
        ) as c,
    ):
        preview = await c.call_tool("dolibarr_time_entry_create", ARGS)
        assert preview.structured_content is not None
        upstream.mode = case
        result = await c.call_tool(
            "dolibarr_time_entry_create",
            {
                **ARGS,
                "apply": True,
                "confirmation_token": preview.structured_content["confirmation_token"],
            },
        )
    assert not result.is_error
    assert result.structured_content is not None
    assert result.structured_content["outcome"] == "partial"
    assert result.structured_content["partial_errors"]
    assert len(upstream.writes) == 1
    assert "hidden" not in str(result)
    assert (result.structured_content["time_entry"] is not None) == (
        case in {"timeout_saved", "bad_json"}
    )


async def test_daily_duplicate_warning_and_empty_comment(settings: Settings) -> None:
    upstream = TimeUpstream()
    args = {**ARGS, "comment": ""}
    upstream.lines = [entry(3, timespent_line_note=None)]
    async with (
        asgi_client(settings, httpx2.MockTransport(upstream.handler), token=upstream.token) as http,
        Client(
            streamable_http_client("http://localhost/mcp", http_client=http), mode="legacy"
        ) as c,
    ):
        preview = await c.call_tool("dolibarr_time_entry_create", args)
        assert preview.structured_content is not None
        assert "Possible duplicate time entry 3." in preview.structured_content["warnings"]
        result = await c.call_tool(
            "dolibarr_time_entry_create",
            {
                **args,
                "apply": True,
                "confirmation_token": preview.structured_content["confirmation_token"],
            },
        )
    assert not result.is_error
    assert result.structured_content is not None
    assert result.structured_content["outcome"] == "applied"


@pytest.mark.parametrize(
    "fields",
    [
        {"task_id": 0},
        {"hours": 0},
        {"hours": -1},
        {"hours": "24.01"},
        {"hours": "NaN"},
        {"hours": "Infinity"},
        {"hours": True},
        {"hours": "0.0001"},
        {"hours": "1.2345"},
        {"hours": "0.000000001"},
        {"work_date": "2026-02-30"},
        {"work_date": "1969-12-31"},
        {"comment": "x" * 4001},
    ],
)
async def test_daily_invalid_inputs_do_not_read_domain(
    settings: Settings, fields: dict[str, object]
) -> None:
    with pytest.raises(ValidationError):
        TimeEntryCreateInput.model_validate({**ARGS, **fields})
    upstream = TimeUpstream()
    async with (
        asgi_client(settings, httpx2.MockTransport(upstream.handler), token=upstream.token) as http,
        Client(
            streamable_http_client("http://localhost/mcp", http_client=http), mode="legacy"
        ) as c,
    ):
        result = await c.call_tool("dolibarr_time_entry_create", {**ARGS, **fields})
    assert result.is_error
    assert all(r.url.path.endswith("/users/info") for r in upstream.calls)


def test_daily_exact_decimal_seconds() -> None:
    data = TimeEntryCreateInput.model_validate({**ARGS, "hours": Decimal("1.005")})
    assert data.duration_seconds == 3618


@pytest.mark.parametrize("field", ["api_key", "user_id", "progress"])
async def test_daily_extra_fields_cannot_override_fixed_payload(
    settings: Settings, field: str
) -> None:
    # The SDK ignores unknown tool arguments; the domain model forbids extra fields.
    args = {**ARGS, field: "99"}
    with pytest.raises(ValidationError):
        TimeEntryCreateInput.model_validate(args)
    upstream = TimeUpstream()
    async with (
        asgi_client(settings, httpx2.MockTransport(upstream.handler), token=upstream.token) as http,
        Client(
            streamable_http_client("http://localhost/mcp", http_client=http), mode="legacy"
        ) as c,
    ):
        preview = await c.call_tool("dolibarr_time_entry_create", args)
        assert preview.structured_content is not None
        result = await c.call_tool(
            "dolibarr_time_entry_create",
            {
                **args,
                "apply": True,
                "confirmation_token": preview.structured_content["confirmation_token"],
            },
        )
    assert not result.is_error
    assert upstream.writes == [
        {
            "date": "2026-11-09 00:00:00",
            "duration": 9000,
            "user_id": 0,
            "note": ARGS["comment"],
            "progress": -1,
        }
    ]
