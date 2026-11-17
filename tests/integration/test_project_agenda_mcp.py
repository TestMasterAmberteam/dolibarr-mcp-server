"""Full authenticated HTTP/MCP flow with stateful official project/agenda route mocks."""

from __future__ import annotations

import json
import logging
import re
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from typing import Any, cast

import httpx2
import pytest
from mcp import Client
from mcp.client.streamable_http import streamable_http_client

from dolibarr_mcp.config import Settings
from tests.integration.test_http_mcp import IDENTITIES, asgi_client

pytestmark = [pytest.mark.anyio, pytest.mark.integration]

CREATE: dict[str, object] = {
    "title": "Mail przypominajacy zostal wyslany",
    "event_type": "email",
    "project_id": 10,
    "start": "2026-11-17",
    "timezone": "Europe/Warsaw",
    "status": "done",
    "description": "Recorded communication, not an outgoing message",
}


def event(event_id: int, **changes: object) -> dict[str, object]:
    return {
        "id": event_id,
        "label": "History",
        "type_code": "AC_OTH_AUTO",
        "type": "systemauto",
        "code": "PROJECT_MODIFY",
        "type_label": "Automatic",
        "datep": 1_792_272_400,
        "datef": 1_792_272_500,
        "fulldayevent": 0,
        "percentage": 100,
        "userownerid": 1,
        "fk_project": 10,
        "socid": 1,
        "note_private": "Safe description",
        "datem": "123",
        "email_to": "secret@example.invalid",
        "array_options": {"hidden": "hidden"},
        **changes,
    }


class Upstream:
    """Deliberately broad list access and authoritative per-event permission checks."""

    def __init__(self) -> None:
        self.projects: dict[int, dict[str, object]] = {
            10: {
                "id": 10,
                "ref": "L2026_0029",
                "title": "Ordinary",
                "socid": 1,
                "usage_opportunity": 0,
                "status": 2,
                "description": "Project description",
                "note_public": "Project note",
                "note_private": "p" * 5000,
                "tms": "123",
            },
            11: {
                "id": 11,
                "ref": "LEAD11",
                "title": "Lead",
                "socid": 1,
                "usage_opportunity": 1,
                "status": 0,
            },
            12: {"id": 12, "ref": "P12", "title": "Other", "usage_opportunity": 0, "status": 1},
        }
        self.events = {
            1: event(1),
            2: event(2, type_code="AC_EMAIL", type="system", percentage=50),
            3: event(3, label="hidden", userownerid=2),
        }
        self.calls: list[httpx2.Request] = []
        self.writes: list[tuple[str, dict[str, object]]] = []
        self.denied_events = {3}
        self.failures: dict[tuple[str, str], int] = {}
        self.mode = "ok"
        self.project_get_count = 0
        self.event_get_count = 0
        self.partner: dict[str, object] = {"id": 1, "name": "Partner"}
        self.list_mode = "ok"

    def handler(self, request: httpx2.Request) -> httpx2.Response:  # noqa: PLR0911, PLR0912, PLR0915 - explicit route/state test double
        self.calls.append(request)
        token = request.headers["DOLAPIKEY"]
        assert token in IDENTITIES
        path = request.url.path.removeprefix("/dolibarr/api/index.php/")
        status = self.failures.get((request.method, path))
        if status:
            return httpx2.Response(status, text="hidden server payload fake-token-A")
        if path == "users/info":
            return httpx2.Response(200, json=IDENTITIES[token])
        if path == "projects":
            page = int(request.url.params["page"])
            rows = sorted(self.projects.values(), key=lambda r: int(str(r["id"])))
            return httpx2.Response(200, json=rows[page * 100 : (page + 1) * 100])
        if path.startswith("projects/"):
            if path.endswith("/contacts"):
                return httpx2.Response(200, json=[])
            self.project_get_count += 1
            row = self.projects.get(int(path.split("/")[1]))
            return httpx2.Response(200 if row else 404, json=row)
        if path == "thirdparties/1":
            return httpx2.Response(200, json=self.partner)
        if path == "agendaevents" and request.method == "GET":
            assert request.url.params["sortfield"] == "t.id"
            assert request.url.params["sortorder"] == "ASC"
            assert "user_ids" not in request.url.params
            assert "email_to" not in request.url.params["properties"]
            rows = sorted(self.events.values(), key=lambda r: int(str(r["id"])))
            criteria = request.url.params.get("sqlfilters", "")
            if criteria:
                assert re.fullmatch(
                    r"\(t\.fk_(?:project|soc):=:\d+\)(?: and \(t\.fk_soc:=:\d+\))?", criteria
                )
                for column, value in re.findall(r"\(t\.(fk_project|fk_soc):=:(\d+)\)", criteria):
                    key = "socid" if column == "fk_soc" else column
                    rows = [row for row in rows if row.get(key) == int(value)]
            page = int(request.url.params["page"])
            if self.list_mode == "repeated":
                rows = [event(1), event(1)]
            if self.list_mode == "reversed":
                rows.reverse()
            return httpx2.Response(200, json=rows[page * 100 : (page + 1) * 100])
        if path == "agendaevents" and request.method == "POST":
            payload = json.loads(request.content)
            self.writes.append(("POST", payload))
            if self.mode not in {"timeout", "server_error"}:
                row = event(42, **payload, type="system")
                if self.mode == "mismatch":
                    row["percentage"] = 0
                self.events[42] = row
                if self.mode == "ambiguous":
                    self.events[43] = {**row, "id": 43}
            if self.mode in {"timeout", "timeout_saved", "ambiguous"}:
                message = "hidden fake-token-A"
                raise httpx2.ReadTimeout(message, request=request)
            if self.mode in {"server_error", "server_error_saved"}:
                return httpx2.Response(500, text="hidden fake-token-A")
            if self.mode == "bad_json":
                return httpx2.Response(200, text="hidden fake-token-A")
            return httpx2.Response(200, json=42)
        if path.startswith("agendaevents/"):
            event_id = int(path.split("/")[1])
            if request.method == "DELETE":
                self.writes.append(("DELETE", {"event_id": event_id}))
                if self.mode != "delete_noop":
                    self.events.pop(event_id, None)
                if self.mode == "delete_timeout":
                    message = "hidden"
                    raise httpx2.ReadTimeout(message, request=request)
                return httpx2.Response(200, json={"success": {"code": 200}})
            self.event_get_count += 1
            if self.writes and self.mode == "read_fail":
                return httpx2.Response(503, text="hidden")
            if self.writes and self.mode == "read_forbidden":
                return httpx2.Response(403, text="hidden")
            if event_id in self.denied_events:
                return httpx2.Response(403, text="hidden")
            row = self.events.get(event_id)
            if row is not None and self.mode == "wrong_id":
                row = {**row, "id": 999}
            return httpx2.Response(200 if row is not None else 404, json=row)
        raise AssertionError((request.method, path))


@asynccontextmanager
async def session(
    settings: Settings,
    upstream: Upstream,
    token: str = "fake-token-A",  # noqa: S107
) -> AsyncIterator[Client]:
    async with (
        asgi_client(settings, httpx2.MockTransport(upstream.handler), token=token) as http,
        Client(
            streamable_http_client("http://localhost/mcp", http_client=http), mode="legacy"
        ) as c,
    ):
        yield c


async def call(c: Client, name: str, args: dict[str, object]) -> dict[str, Any]:
    result = await c.call_tool(name, args)
    assert not result.is_error, str(result)
    assert result.structured_content is not None
    return cast("dict[str, Any]", result.structured_content)


async def test_projects_and_unchanged_leads(settings: Settings) -> None:
    upstream = Upstream()
    async with session(settings, upstream) as c:
        all_rows = await call(c, "dolibarr_project_search", {"limit": 1})
        assert all_rows["total_count"] == 3
        assert all_rows["has_more"]
        assert all_rows["rows"][0]["usage_opportunity"] is False
        assert all_rows["rows"][0]["project_state"] == "closed"
        second = await call(c, "dolibarr_project_search", {"offset": 1, "limit": 1})
        assert second["rows"][0]["project_id"] == 11
        selected = await call(
            c,
            "dolibarr_project_search",
            {
                "query": "l2026_0029",
                "project_state": "closed",
                "usage_opportunity": False,
                "thirdparty_id": 1,
            },
        )
        assert selected["total_count"] == 1
        detail = await call(c, "dolibarr_project_get", {"project_id": 10})
        assert detail["description"] == "Project description"
        assert len(detail["private_note"]) == 4000
        leads = await call(c, "dolibarr_lead_search", {})
        assert [row["project_id"] for row in leads["rows"]] == [11]
        assert (await c.call_tool("dolibarr_lead_get", {"project_id": 10})).is_error


@pytest.mark.parametrize(
    "args",
    [
        {"query": "missing"},
        {"thirdparty_id": 99},
        {"usage_opportunity": True},
        {"project_state": "open"},
        {"offset": 99},
    ],
)
async def test_project_filters(settings: Settings, args: dict[str, object]) -> None:
    async with session(settings, Upstream()) as c:
        result = await call(c, "dolibarr_project_search", args)
    assert result["returned_count"] <= 1


async def test_history_includes_automatic_and_authorizes_each_row(settings: Settings) -> None:
    upstream = Upstream()
    async with session(settings, upstream) as c:
        history = await call(c, "dolibarr_agenda_event_search", {"project_id": 10, "limit": 1})
        assert history["total_count"] == 2
        assert history["has_more"]
        assert history["rows"][0]["automatic"] is True
        assert history["rows"][0]["event_code"] == "PROJECT_MODIFY"
        manual = await call(
            c,
            "dolibarr_agenda_event_search",
            {
                "project_id": 10,
                "thirdparty_id": 1,
                "owner_user_id": 1,
                "type_code": "AC_EMAIL",
                "status": "in_progress",
            },
        )
        assert [row["event_id"] for row in manual["rows"]] == [2]
        detail = await call(c, "dolibarr_agenda_event_get", {"event_id": 1})
    assert detail["description"] == "Safe description"
    assert "hidden" not in str(history)
    assert "secret" not in str(detail)
    assert sum(r.url.path.endswith("/users/info") for r in upstream.calls) == 6


@pytest.mark.parametrize("token", ["fake-token-A", "fake-token-B"])
async def test_email_preview_apply_replay(settings: Settings, token: str) -> None:
    upstream = Upstream()
    async with session(settings, upstream, token) as c:
        preview = await call(c, "dolibarr_agenda_event_create", CREATE)
        assert preview["phase"] == "preview"
        assert upstream.writes == []
        changes = {row["field"]: row["after"] for row in preview["changes"]}
        assert changes["date_start"] == "2026-11-17T00:00:00+01:00"
        assert changes["date_end"] == "2026-11-17T23:59:59+01:00"
        args = {**CREATE, "apply": True, "confirmation_token": preview["confirmation_token"]}
        result = await call(c, "dolibarr_agenda_event_create", args)
        assert (await c.call_tool("dolibarr_agenda_event_create", args)).is_error
    assert result["outcome"] == "applied"
    assert result["target_id"] == 42
    row = result["agenda_event"]
    assert row["owner_user_id"] == IDENTITIES[token]["id"]
    assert row["percentage"] == 100
    assert row["type_code"] == "AC_EMAIL"
    assert row["project_id"] == 10
    assert row["thirdparty_id"] == 1
    assert row["all_day"]
    assert len(upstream.writes) == 1
    payload = upstream.writes[0][1]
    assert set(payload) == {
        "label",
        "type_code",
        "fk_project",
        "socid",
        "userownerid",
        "datep",
        "datef",
        "fulldayevent",
        "percentage",
        "note_private",
    }
    assert payload["datep"] == int(datetime(2026, 11, 16, 23, tzinfo=UTC).timestamp())


@pytest.mark.parametrize("change", ["missing", "wrong", "title", "project", "partner", "duplicate"])
async def test_stale_create(settings: Settings, change: str) -> None:
    upstream = Upstream()
    async with session(settings, upstream) as c:
        preview = await call(c, "dolibarr_agenda_event_create", CREATE)
        args = {**CREATE, "apply": True, "confirmation_token": preview["confirmation_token"]}
        if change == "missing":
            args.pop("confirmation_token")
        elif change == "wrong":
            args["confirmation_token"] = "a" * 64
        elif change == "title":
            args["title"] = "Changed"
        elif change == "project":
            upstream.projects[10]["tms"] = "999"
        elif change == "partner":
            upstream.partner["name"] = "Changed"
        else:
            upstream.events[42] = event(
                42,
                label=CREATE["title"],
                type_code="AC_EMAIL",
                type="system",
                datep=1794870000,
                datef=1794956399,
                fulldayevent=1,
                note_private=CREATE["description"],
            )
        result = await c.call_tool("dolibarr_agenda_event_create", args)
    assert result.is_error
    assert upstream.writes == []


@pytest.mark.parametrize(
    ("mode", "saved"),
    [
        ("timeout", False),
        ("timeout_saved", True),
        ("server_error", False),
        ("server_error_saved", True),
        ("bad_json", True),
        ("ambiguous", True),
        ("mismatch", True),
        ("read_fail", True),
        ("read_forbidden", True),
    ],
)
async def test_uncertain_create_never_retries(
    settings: Settings,
    caplog: pytest.LogCaptureFixture,
    mode: str,
    saved: bool,  # noqa: FBT001 - pytest parameter
) -> None:
    upstream = Upstream()
    async with session(settings, upstream) as c:
        preview = await call(c, "dolibarr_agenda_event_create", CREATE)
        upstream.mode = mode
        with caplog.at_level(logging.DEBUG):
            result = await call(
                c,
                "dolibarr_agenda_event_create",
                {
                    **CREATE,
                    "apply": True,
                    "confirmation_token": preview["confirmation_token"],
                },
            )
    assert result["outcome"] == "partial"
    assert result["partial_errors"]
    assert len(upstream.writes) == 1
    assert (42 in upstream.events) == saved
    assert "fake-token-A" not in caplog.text
    assert "hidden" not in caplog.text
    if mode in {"timeout_saved", "server_error_saved", "bad_json"}:
        assert result["matching_event_ids"] == [42]
        assert result["agenda_event"]["event_id"] == 42
    if mode == "ambiguous":
        assert result["matching_event_ids"] == [42, 43]
        assert result["target_id"] is None


@pytest.mark.parametrize(
    "mode", ["ok", "delete_noop", "delete_timeout", "read_fail", "read_forbidden"]
)
async def test_delete_exact_record_and_verify(settings: Settings, mode: str) -> None:
    upstream = Upstream()
    async with session(settings, upstream) as c:
        preview = await call(c, "dolibarr_agenda_event_delete", {"event_id": 1})
        assert preview["agenda_event"]["title"] == "History"
        assert upstream.writes == []
        upstream.mode = mode
        result = await call(
            c,
            "dolibarr_agenda_event_delete",
            {
                "event_id": 1,
                "apply": True,
                "confirmation_token": preview["confirmation_token"],
            },
        )
    assert upstream.writes == [("DELETE", {"event_id": 1})]
    assert 2 in upstream.events
    assert 3 in upstream.events
    assert result["outcome"] == ("applied" if mode == "ok" else "partial")


@pytest.mark.parametrize("field", ["label", "datem", "note_private", "userownerid", "fk_project"])
async def test_delete_stale_record(settings: Settings, field: str) -> None:
    upstream = Upstream()
    upstream.events[1]["note_private"] = "x" * 5000
    async with session(settings, upstream) as c:
        preview = await call(c, "dolibarr_agenda_event_delete", {"event_id": 1})
        upstream.events[1][field] = {
            "label": "Changed",
            "datem": "999",
            "note_private": "x" * 5001,
            "userownerid": 2,
            "fk_project": 11,
        }[field]
        result = await c.call_tool(
            "dolibarr_agenda_event_delete",
            {
                "event_id": 1,
                "apply": True,
                "confirmation_token": preview["confirmation_token"],
            },
        )
    assert result.is_error
    assert upstream.writes == []


@pytest.mark.parametrize("status", [400, 401, 403, 404, 409, 422, 429])
@pytest.mark.parametrize("operation", ["create", "delete"])
async def test_definite_mutation_errors(settings: Settings, status: int, operation: str) -> None:
    upstream = Upstream()
    tool = f"dolibarr_agenda_event_{operation}"
    args: dict[str, object] = CREATE if operation == "create" else {"event_id": 1}
    async with session(settings, upstream) as c:
        preview = await call(c, tool, args)
        upstream.failures[
            ("POST", "agendaevents") if operation == "create" else ("DELETE", "agendaevents/1")
        ] = status
        result = await c.call_tool(
            tool,
            {
                **args,
                "apply": True,
                "confirmation_token": preview["confirmation_token"],
            },
        )
    assert result.is_error
    assert "hidden" not in str(result)


@pytest.mark.parametrize(
    ("tool", "args"),
    [
        ("dolibarr_agenda_event_create", {**CREATE, "thirdparty_id": 99}),
        ("dolibarr_agenda_event_create", {**CREATE, "event_type": "AC_OTH_AUTO"}),
        ("dolibarr_agenda_event_create", {**CREATE, "project_id": -1}),
        ("dolibarr_agenda_event_create", {**CREATE, "start": "2026-03-29T02:30:00"}),
        ("dolibarr_agenda_event_get", {"event_id": 999}),
        ("dolibarr_agenda_event_get", {"event_id": 3}),
        ("dolibarr_agenda_event_get", {"event_id": 1, "timezone": "bad/zone"}),
        ("dolibarr_agenda_event_delete", {"event_id": 0}),
        ("dolibarr_agenda_event_search", {"date_from": "2026-11-18", "date_to": "2026-11-17"}),
        ("dolibarr_agenda_event_search", {"timezone": "bad/zone"}),
        ("dolibarr_agenda_event_search", {"project_id": 999}),
    ],
)
async def test_invalid_and_inaccessible(
    settings: Settings, tool: str, args: dict[str, object]
) -> None:
    upstream = Upstream()
    async with session(settings, upstream) as c:
        result = await c.call_tool(tool, args)
    assert result.is_error
    assert upstream.writes == []


@pytest.mark.parametrize("kind", ["agenda", "projects"])
async def test_scan_bound_is_explicit(
    settings: Settings, monkeypatch: pytest.MonkeyPatch, kind: str
) -> None:
    upstream = Upstream()
    if kind == "agenda":
        monkeypatch.setattr("dolibarr_mcp.client._MAX_AGENDA_EVENTS", 100)
        upstream.events = {i: event(i) for i in range(1, 101)}
        tool = "dolibarr_agenda_event_search"
    else:
        monkeypatch.setattr("dolibarr_mcp.client._MAX_SALES_RECORDS", 100)
        upstream.projects = {i: {"id": i, "ref": str(i), "title": str(i)} for i in range(1, 101)}
        tool = "dolibarr_project_search"
    async with session(settings, upstream) as c:
        result = await c.call_tool(tool, {})
    assert result.is_error
    assert "limit" in str(result).lower()
    assert upstream.writes == []


@pytest.mark.parametrize("mode", ["repeated", "reversed"])
async def test_invalid_pagination(settings: Settings, mode: str) -> None:
    upstream = Upstream()
    upstream.list_mode = mode
    async with session(settings, upstream) as c:
        assert (await c.call_tool("dolibarr_agenda_event_search", {})).is_error


@pytest.mark.parametrize(
    ("event_type", "status", "code", "percentage"),
    [
        ("phone_call", "not_started", "AC_TEL", 0),
        ("meeting", "in_progress", "AC_RDV", 50),
        ("other", "not_applicable", "AC_OTH", -1),
    ],
)
async def test_manual_types_timed_events_and_project_without_partner(
    settings: Settings, event_type: str, status: str, code: str, percentage: int
) -> None:
    upstream = Upstream()
    args: dict[str, object] = {
        "project_id": 12,
        "title": "Timed",
        "start": "2026-11-17T10:00:00",
        "end": "2026-11-17T10:30:00",
        "timezone": "Europe/Warsaw",
        "event_type": event_type,
        "status": status,
    }
    async with session(settings, upstream) as c:
        preview = await call(c, "dolibarr_agenda_event_create", args)
        result = await call(
            c,
            "dolibarr_agenda_event_create",
            {
                **args,
                "apply": True,
                "confirmation_token": preview["confirmation_token"],
            },
        )
        history = await call(
            c, "dolibarr_agenda_event_search", {"project_id": 12, "status": status}
        )
    assert result["outcome"] == "applied"
    assert result["agenda_event"]["thirdparty_id"] is None
    assert result["agenda_event"]["all_day"] is False
    assert result["agenda_event"]["percentage"] == percentage
    assert history["rows"][0]["type_code"] == code


@pytest.mark.parametrize("status", [403, 404])
@pytest.mark.parametrize("path", ["projects/10", "thirdparties/1", "agendaevents/1"])
async def test_history_cannot_expose_inaccessible_related_records(
    settings: Settings, status: int, path: str
) -> None:
    upstream = Upstream()
    upstream.events[4] = event(4, fk_project=12, socid=0)
    upstream.failures[("GET", path)] = status
    async with session(settings, upstream) as c:
        history = await call(c, "dolibarr_agenda_event_search", {})
        direct = await c.call_tool("dolibarr_agenda_event_get", {"event_id": 1})
    assert direct.is_error
    assert 1 not in [row["event_id"] for row in history["rows"]]
    assert 4 in [row["event_id"] for row in history["rows"]]


async def test_unknown_dates_and_links_are_safe(settings: Settings) -> None:
    upstream = Upstream()
    upstream.events[4] = event(
        4,
        fk_project=0,
        socid=0,
        userownerid=None,
        datep=None,
        datef=None,
        note_private="x" * 5000,
        note_public="y" * 5000,
    )
    async with session(settings, upstream) as c:
        detail = await call(c, "dolibarr_agenda_event_get", {"event_id": 4})
        preview = await call(c, "dolibarr_agenda_event_delete", {"event_id": 4})
    assert detail["date_start"] is None
    assert detail["date_end"] is None
    assert len(detail["description"]) == 4000
    assert len(detail["public_note"]) == 4000
    assert preview["agenda_event"]["owner_user_id"] is None


@pytest.mark.parametrize(
    ("date_from", "date_to", "expected"),
    [
        ("2026-11-17", "2026-11-17", [1]),
        (None, "2026-11-16", [1, 2, 4]),
        ("2026-11-18", None, [3]),
        ("1970-01-01", "1970-01-01", [4]),
    ],
)
async def test_date_range_overlap_and_epoch(
    settings: Settings, date_from: str | None, date_to: str | None, expected: list[int]
) -> None:
    upstream = Upstream()
    stamp = int(datetime(2026, 11, 17, tzinfo=UTC).timestamp())
    upstream.denied_events.clear()
    upstream.events = {
        1: event(1, datep=stamp - 3600, datef=stamp + 3600),
        2: event(2, datep=stamp - 86400, datef=None),
        3: event(3, datep=stamp + 86400, datef=None),
        4: event(4, datep=0, datef=None),
        5: event(5, datep=None, datef=None),
    }
    async with session(settings, upstream) as c:
        result = await call(
            c,
            "dolibarr_agenda_event_search",
            {
                "date_from": date_from,
                "date_to": date_to,
            },
        )
    assert [row["event_id"] for row in result["rows"]] == expected


async def test_project_get_denial_and_wrong_returned_ids(settings: Settings) -> None:
    upstream = Upstream()
    upstream.failures[("GET", "projects/11")] = 403
    upstream.projects[10]["id"] = 999
    async with session(settings, upstream) as c:
        result = await call(c, "dolibarr_project_search", {})
        assert [row["project_id"] for row in result["rows"]] == [12]
        assert (await c.call_tool("dolibarr_project_get", {"project_id": 10})).is_error
        upstream.projects[10]["id"] = 10
        upstream.partner["id"] = 99
        assert (await c.call_tool("dolibarr_agenda_event_get", {"event_id": 1})).is_error
        assert (await c.call_tool("dolibarr_agenda_event_create", CREATE)).is_error
        upstream.mode = "wrong_id"
        assert (await c.call_tool("dolibarr_agenda_event_get", {"event_id": 2})).is_error


async def test_confirmation_cannot_cross_users(settings: Settings) -> None:
    upstream = Upstream()
    async with session(settings, upstream) as c:
        create = await call(c, "dolibarr_agenda_event_create", CREATE)
        delete = await call(c, "dolibarr_agenda_event_delete", {"event_id": 1})
    async with session(settings, upstream, "fake-token-B") as c:
        result = await c.call_tool(
            "dolibarr_agenda_event_create",
            {
                **CREATE,
                "apply": True,
                "confirmation_token": create["confirmation_token"],
            },
        )
        assert result.is_error
        result = await c.call_tool(
            "dolibarr_agenda_event_delete",
            {
                "event_id": 1,
                "apply": True,
                "confirmation_token": delete["confirmation_token"],
            },
        )
        assert result.is_error
    assert upstream.writes == []


async def test_safe_validation_error_message(
    settings: Settings, caplog: pytest.LogCaptureFixture
) -> None:
    upstream = Upstream()
    async with session(settings, upstream) as c:
        with caplog.at_level(logging.DEBUG):
            result = await c.call_tool(
                "dolibarr_agenda_event_create",
                {
                    **CREATE,
                    "start": "2026-10-25T02:30:00",
                    "description": "unique-business-text-never-in-logs",
                },
            )
    assert result.is_error
    server_logs = "\n".join(
        record.getMessage()
        for record in caplog.records
        if record.name.startswith(("mcp.server", "dolibarr_mcp"))
    )
    assert "unique-business-text" not in server_logs
    assert "unique-business-text" not in str(result)


async def test_multiple_upstream_pages_and_output_pages(settings: Settings) -> None:
    upstream = Upstream()
    upstream.denied_events.clear()
    upstream.events = {i: event(i) for i in range(1, 104)}
    async with session(settings, upstream) as c:
        first = await call(c, "dolibarr_agenda_event_search", {"project_id": 10, "limit": 100})
        last = await call(
            c,
            "dolibarr_agenda_event_search",
            {
                "project_id": 10,
                "offset": 100,
                "limit": 100,
            },
        )
    assert first["total_count"] == 103
    assert first["has_more"]
    assert [row["event_id"] for row in last["rows"]] == [101, 102, 103]
    assert not last["has_more"]
    pages = [r.url.params["page"] for r in upstream.calls if r.url.path.endswith("/agendaevents")]
    assert pages == ["0", "1", "0", "1"]


async def test_project_history_works_above_global_agenda_bound(
    settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    upstream = Upstream()
    upstream.denied_events.clear()
    upstream.events = {i: event(i) for i in range(1, 102)}
    upstream.events[200] = event(200, fk_project=12, socid=0)
    monkeypatch.setattr("dolibarr_mcp.client._MAX_AGENDA_EVENTS", 100)
    async with session(settings, upstream) as c:
        selected = await call(c, "dolibarr_agenda_event_search", {"project_id": 12})
        assert [row["event_id"] for row in selected["rows"]] == [200]
        customer = await call(
            c, "dolibarr_agenda_event_search", {"thirdparty_id": 1, "project_id": 12}
        )
        assert customer["rows"] == []
        assert (await c.call_tool("dolibarr_agenda_event_search", {})).is_error
    criteria = [
        r.url.params.get("sqlfilters")
        for r in upstream.calls
        if r.url.path.endswith("/agendaevents")
    ]
    assert criteria == ["(t.fk_project:=:12)", "(t.fk_project:=:12) and (t.fk_soc:=:1)", None]
