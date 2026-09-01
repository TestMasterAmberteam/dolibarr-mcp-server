"""Dolibarr client isolation, mapping, and payload tests."""

from __future__ import annotations

import ssl
from pathlib import Path

import httpx2
import pytest

import dolibarr_mcp.client as client_module
from dolibarr_mcp.client import DolibarrClient, validated_retry_after
from dolibarr_mcp.config import Settings
from dolibarr_mcp.errors import (
    DolibarrConflictError,
    DolibarrNotFoundError,
    DolibarrPermissionDeniedError,
    DolibarrRateLimitedError,
    DolibarrResultLimitError,
    DolibarrUnavailableError,
    DolibarrWriteRejectedError,
    InvalidDolibarrCredentialsError,
    InvalidDolibarrResponseError,
)

pytestmark = pytest.mark.anyio


def task_payload(
    task_id: int, *, lines: list[dict[str, object]] | None = None
) -> dict[str, object]:
    return {
        "id": str(task_id),
        "fk_project": "10",
        "ref": f"T-{task_id}",
        "label": f"Task {task_id}",
        "lines": lines,
    }


def time_payload(entry_id: int = 1) -> dict[str, object]:
    return {
        "timespent_line_id": str(entry_id),
        "timespent_line_date": 1_786_000_000,
        "timespent_line_duration": "3600",
        "timespent_line_fk_user": "7",
        "fk_project": "10",
        "project_ref": "P-10",
        "project_label": "Project Ten",
        "fk_task": "20",
        "task_ref": "T-20",
        "task_label": "Task Twenty",
        "timespent_line_note": "work",
    }


async def test_key_is_added_only_to_the_individual_upstream_request(settings: Settings) -> None:
    seen: list[httpx2.Request] = []

    async def handler(request: httpx2.Request) -> httpx2.Response:
        seen.append(request)
        return httpx2.Response(
            200,
            json={"id": "42", "login": "alice", "firstname": "Alice", "lastname": "Doe"},
        )

    client = DolibarrClient(settings, transport=httpx2.MockTransport(handler))
    assert "DOLAPIKEY" not in client._client.headers
    assert "Authorization" not in client._client.headers
    async with client:
        identity = await client.get_current_user("fake-key-A")
    assert identity.user_id == 42
    assert seen[0].url.path == "/dolibarr/api/index.php/users/info"
    assert seen[0].headers["DOLAPIKEY"] == "fake-key-A"
    assert "Authorization" not in seen[0].headers
    assert "DOLAPIKEY" not in client._client.headers


@pytest.mark.parametrize("status_code", [401, 403])
async def test_invalid_credentials_mapping(settings: Settings, status_code: int) -> None:
    transport = httpx2.MockTransport(lambda _request: httpx2.Response(status_code))
    async with DolibarrClient(settings, transport=transport) as client:
        with pytest.raises(InvalidDolibarrCredentialsError):
            await client.get_current_user("invalid-key")


@pytest.mark.parametrize("status_code", [400, 404, 418, 300])
async def test_unexpected_status_mapping(settings: Settings, status_code: int) -> None:
    transport = httpx2.MockTransport(lambda _request: httpx2.Response(status_code))
    async with DolibarrClient(settings, transport=transport) as client:
        with pytest.raises(InvalidDolibarrResponseError):
            await client.get_current_user("fake-key")


@pytest.mark.parametrize("status_code", [500, 502, 599])
async def test_upstream_server_errors_are_unavailable(settings: Settings, status_code: int) -> None:
    transport = httpx2.MockTransport(lambda _request: httpx2.Response(status_code))
    async with DolibarrClient(settings, transport=transport) as client:
        with pytest.raises(DolibarrUnavailableError):
            await client.get_current_user("fake-key")


@pytest.mark.parametrize(
    ("headers", "expected"),
    [
        ({"Retry-After": "120"}, "120"),
        ({"Retry-After": "Wed, 21 Oct 2037 07:28:00 GMT"}, "Wed, 21 Oct 2037 07:28:00 GMT"),
        ({"Retry-After": "not-a-date"}, None),
        ({"Retry-After": "Wed, 21 Oct 2037 07:28:00"}, None),
        ({}, None),
    ],
)
def test_retry_after_validation(headers: dict[str, str], expected: str | None) -> None:
    response = httpx2.Response(429, headers=headers)
    assert validated_retry_after(response) == expected


async def test_rate_limit_preserves_only_valid_retry_after(settings: Settings) -> None:
    transport = httpx2.MockTransport(
        lambda _request: httpx2.Response(429, headers={"Retry-After": "15"})
    )
    async with DolibarrClient(settings, transport=transport) as client:
        with pytest.raises(DolibarrRateLimitedError) as captured:
            await client.get_current_user("fake-key")
    assert captured.value.retry_after == "15"


@pytest.mark.parametrize(
    "payload",
    [
        b"not-json",
        b"[]",
        b'{"id":1}',
        b'{"id":"not-an-int","login":"alice"}',
        b'{"id":1,"login":""}',
    ],
)
async def test_invalid_success_payload_is_bad_gateway(settings: Settings, payload: bytes) -> None:
    transport = httpx2.MockTransport(
        lambda _request: httpx2.Response(
            200, content=payload, headers={"content-type": "application/json"}
        )
    )
    async with DolibarrClient(settings, transport=transport) as client:
        with pytest.raises(InvalidDolibarrResponseError):
            await client.get_current_user("fake-key")


@pytest.mark.parametrize(
    "error",
    [httpx2.ConnectError("offline"), httpx2.ReadTimeout("slow")],
)
async def test_network_and_timeout_failures_are_unavailable(
    settings: Settings, error: httpx2.RequestError
) -> None:
    def handler(request: httpx2.Request) -> httpx2.Response:
        error.request = request
        raise error

    async with DolibarrClient(settings, transport=httpx2.MockTransport(handler)) as client:
        with pytest.raises(DolibarrUnavailableError):
            await client.get_current_user("fake-key")


def test_custom_ca_builds_ssl_context(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    ca_bundle = tmp_path / "ca.pem"
    ca_bundle.write_text("test fixture", encoding="utf-8")
    ca_settings = Settings.model_validate(
        {
            "DOLIBARR_BASE_URL": "https://erp.example.org",
            "DOLIBARR_CA_BUNDLE": ca_bundle,
        }
    )
    original = ssl.create_default_context
    seen: list[str | None] = []

    def fake_context(*, cafile: str | None = None) -> ssl.SSLContext:
        seen.append(cafile)
        return original()

    monkeypatch.setattr("dolibarr_mcp.client.ssl.create_default_context", fake_context)
    DolibarrClient(ca_settings)
    assert seen == [str(ca_bundle)]


async def test_reporting_gets_use_fixed_paths_and_individual_credentials(
    settings: Settings,
) -> None:
    seen: list[httpx2.Request] = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        seen.append(request)
        path = request.url.path
        if path.endswith("/projects/10"):
            return httpx2.Response(200, json={"id": "10", "ref": "P-10", "title": "Project"})
        if path.endswith("/projects/10/tasks"):
            return httpx2.Response(200, json=[task_payload(20, lines=[time_payload()])])
        if path.endswith("/tasks/20/timespent"):
            return httpx2.Response(200, json=[time_payload()])
        if path.endswith("/tasks/20"):
            return httpx2.Response(200, json=task_payload(20))
        if path.endswith("/tasks"):
            return httpx2.Response(200, json=[task_payload(20)])
        if path.endswith("/users"):
            return httpx2.Response(
                200,
                json=[{"id": "7", "login": "alice", "firstname": "Alice"}],
            )
        raise AssertionError(path)

    async with DolibarrClient(settings, transport=httpx2.MockTransport(handler)) as client:
        project = await client.get_project("report-key", 10)
        task = await client.get_task("report-key", 20)
        lines = await client.get_task_timespent("report-key", 20)
        project_tasks = await client.get_project_tasks("report-key", 10)
        tasks = await client.list_tasks("report-key")
        users = await client.list_users("report-key")
    assert project.label == "Project"
    assert task.task_id == 20
    assert lines[0].duration_seconds == 3600
    assert project_tasks[0].lines is not None
    assert tasks[0].ref == "T-20"
    assert users[0].login == "alice"
    assert all(request.headers["DOLAPIKEY"] == "report-key" for request in seen)
    assert all("Authorization" not in request.headers for request in seen)
    users_request = next(request for request in seen if request.url.path.endswith("/users"))
    assert users_request.url.params["properties"] == ("id,login,firstname,lastname,status,statut")


async def test_task_listing_paginates_until_a_short_page(settings: Settings) -> None:
    pages: list[int] = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        page = int(request.url.params["page"])
        pages.append(page)
        count = 100 if page == 0 else 1
        start = page * 100
        return httpx2.Response(
            200,
            json=[task_payload(start + index + 1) for index in range(count)],
        )

    async with DolibarrClient(settings, transport=httpx2.MockTransport(handler)) as client:
        tasks = await client.list_tasks("key")
    assert len(tasks) == 101
    assert pages == [0, 1]


@pytest.mark.parametrize(
    ("status_code", "error_type"),
    [(403, DolibarrPermissionDeniedError), (404, DolibarrNotFoundError)],
)
async def test_reporting_resource_errors_are_typed(
    settings: Settings,
    status_code: int,
    error_type: type[Exception],
) -> None:
    transport = httpx2.MockTransport(lambda _request: httpx2.Response(status_code))
    async with DolibarrClient(settings, transport=transport) as client:
        with pytest.raises(error_type):
            await client.get_task("key", 20)


async def test_reporting_bounds_are_enforced(
    settings: Settings,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(client_module, "_MAX_TIME_LINES", 1)
    monkeypatch.setattr(client_module, "_MAX_TASKS", 1)

    def handler(request: httpx2.Request) -> httpx2.Response:
        if request.url.path.endswith("/timespent"):
            return httpx2.Response(200, json=[time_payload(1), time_payload(2)])
        return httpx2.Response(200, json=[task_payload(1), task_payload(2)])

    async with DolibarrClient(settings, transport=httpx2.MockTransport(handler)) as client:
        with pytest.raises(DolibarrResultLimitError):
            await client.get_task_timespent("key", 20)
        with pytest.raises(DolibarrResultLimitError):
            await client.get_project_tasks("key", 10)


async def test_exact_task_page_bound_is_rejected(
    settings: Settings,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(client_module, "_MAX_TASKS", 100)
    transport = httpx2.MockTransport(
        lambda _request: httpx2.Response(
            200,
            json=[task_payload(index + 1) for index in range(100)],
        )
    )
    async with DolibarrClient(settings, transport=transport) as client:
        with pytest.raises(DolibarrResultLimitError):
            await client.list_tasks("key")


async def test_exact_user_page_bound_is_rejected(
    settings: Settings,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(client_module, "_MAX_USERS", 100)
    transport = httpx2.MockTransport(
        lambda _request: httpx2.Response(
            200,
            json=[{"id": index + 1, "login": f"user-{index}"} for index in range(100)],
        )
    )
    async with DolibarrClient(settings, transport=transport) as client:
        with pytest.raises(DolibarrResultLimitError):
            await client.list_users("key")


async def test_sales_client_uses_only_fixed_api_routes_and_payloads(settings: Settings) -> None:
    seen: list[httpx2.Request] = []

    def handler(request: httpx2.Request) -> httpx2.Response:  # noqa: PLR0911
        seen.append(request)
        path = request.url.path
        if path.endswith("/thirdparties") and request.method == "GET":
            return httpx2.Response(200, json=[{"id": 1, "name": "Acme", "client": 2}])
        if path.endswith("/thirdparties") and request.method == "POST":
            return httpx2.Response(200, json="2")
        if path.endswith("/thirdparties/1"):
            return httpx2.Response(200, json={"id": 1, "name": "Acme", "client": 2})
        if path.endswith("/projects") and request.method == "GET":
            return httpx2.Response(
                200,
                json=[
                    {
                        "id": 10,
                        "ref": "PJ-10",
                        "title": "Lead",
                        "usage_opportunity": 1,
                    }
                ],
            )
        if path.endswith("/projects") and request.method == "POST":
            return httpx2.Response(200, json=20)
        if path.endswith("/projects/10/contacts"):
            if request.method == "GET":
                return httpx2.Response(
                    200,
                    json=[
                        {
                            "id": 7,
                            "rowid": 70,
                            "code": "PROJECTLEADER",
                            "source": "internal",
                        }
                    ],
                )
            return httpx2.Response(200, json={"id": 10})
        if path.endswith("/projects/10/contact/7/PROJECTLEADER"):
            return httpx2.Response(200, json={"id": 10})
        if path.endswith("/projects/10/validate"):
            return httpx2.Response(200, json={"success": {"code": 200}})
        if path.endswith("/projects/10"):
            return httpx2.Response(
                200,
                json={
                    "id": 10,
                    "ref": "PJ-10",
                    "title": "Lead",
                    "usage_opportunity": 1,
                },
            )
        if path.endswith("/users/7"):
            return httpx2.Response(200, json={"id": 7, "login": "alice", "status": 1})
        raise AssertionError((request.method, path))

    async with DolibarrClient(
        settings,
        transport=httpx2.MockTransport(handler),
    ) as client:
        thirdparties = await client.list_thirdparties("sales-key")
        thirdparty = await client.get_thirdparty("sales-key", 1)
        created_thirdparty = await client.create_thirdparty(
            "sales-key", {"name": "New", "client": 2}
        )
        updated_thirdparty = await client.update_thirdparty("sales-key", 1, {"town": "Warsaw"})
        projects = await client.list_projects("sales-key")
        created_project = await client.create_project(
            "sales-key", {"ref": "auto", "title": "New Lead"}
        )
        updated_project = await client.update_project("sales-key", 10, {"fk_opp_status": 4})
        contacts = await client.get_project_contacts("sales-key", 10)
        await client.add_project_leader("sales-key", 10, 7)
        await client.delete_project_leader("sales-key", 10, 7)
        await client.validate_project("sales-key", 10)
        selected_user = await client.get_user("sales-key", 7)

    assert thirdparties[0].customer_classification == 2
    assert thirdparty.name == "Acme"
    assert created_thirdparty == 2
    assert updated_thirdparty.thirdparty_id == 1
    assert projects[0].usage_opportunity is True
    assert created_project == 20
    assert updated_project.project_id == 10
    assert contacts[0].row_id == 70
    assert selected_user.login == "alice"
    assert all(request.headers["DOLAPIKEY"] == "sales-key" for request in seen)
    assert all("Authorization" not in request.headers for request in seen)
    assert any(request.method == "DELETE" for request in seen)
    create_request = next(
        request
        for request in seen
        if request.method == "POST" and request.url.path.endswith("/thirdparties")
    )
    assert create_request.content == b'{"name":"New","client":2}'
    assert all("sqlfilters" not in request.url.params for request in seen)


@pytest.mark.parametrize(
    ("status_code", "error_type"),
    [
        (409, DolibarrConflictError),
        (400, DolibarrWriteRejectedError),
        (422, DolibarrWriteRejectedError),
    ],
)
async def test_sales_write_errors_are_sanitized_and_typed(
    settings: Settings,
    status_code: int,
    error_type: type[Exception],
) -> None:
    transport = httpx2.MockTransport(lambda _request: httpx2.Response(status_code))
    async with DolibarrClient(settings, transport=transport) as client:
        with pytest.raises(error_type):
            await client.create_thirdparty("key", {"name": "Rejected"})


async def test_sales_list_bound_is_enforced(
    settings: Settings,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(client_module, "_MAX_SALES_RECORDS", 100)

    def handler(request: httpx2.Request) -> httpx2.Response:
        if request.url.path.endswith("/thirdparties"):
            return httpx2.Response(
                200,
                json=[{"id": index + 1, "name": f"Company {index}"} for index in range(100)],
            )
        return httpx2.Response(
            200,
            json=[
                {"id": index + 1, "ref": f"P-{index}", "title": f"Project {index}"}
                for index in range(100)
            ],
        )

    async with DolibarrClient(settings, transport=httpx2.MockTransport(handler)) as client:
        with pytest.raises(DolibarrResultLimitError):
            await client.list_thirdparties("key")
        with pytest.raises(DolibarrResultLimitError):
            await client.list_projects("key")


async def test_leave_client_uses_only_fixed_api_routes_and_payloads(
    settings: Settings,
) -> None:
    seen: list[httpx2.Request] = []

    def leave_payload(status: int = 1) -> dict[str, object]:
        return {
            "id": 1,
            "ref": "LR-1",
            "fk_user": 7,
            "fk_validator": 8,
            "fk_type": 2,
            "date_debut": 1_786_665_600,
            "date_fin": 1_786_752_000,
            "halfday": 0,
            "status": status,
        }

    def handler(request: httpx2.Request) -> httpx2.Response:
        seen.append(request)
        path = request.url.path
        if path.endswith("/setup/dictionary/holiday_types"):
            return httpx2.Response(
                200,
                json=[
                    {
                        "rowid": 2,
                        "code": "PAID",
                        "label": "Paid leave",
                        "affect": 1,
                    }
                ],
            )
        if path.endswith("/holidays") and request.method == "GET":
            return httpx2.Response(200, json=[leave_payload()])
        if path.endswith("/holidays") and request.method == "POST":
            return httpx2.Response(200, json="6")
        if path.endswith("/holidays/1") and request.method == "GET":
            return httpx2.Response(200, json=leave_payload())
        if path.endswith("/holidays/1") and request.method == "PUT":
            return httpx2.Response(200, json=leave_payload())
        statuses = {
            "validate": 2,
            "approve": 3,
            "cancel": 4,
            "refuse": 5,
            "reopen": 2,
        }
        for action, status in statuses.items():
            if path.endswith(f"/holidays/1/{action}"):
                return httpx2.Response(200, json=leave_payload(status))
        raise AssertionError((request.method, path))

    async with DolibarrClient(
        settings,
        transport=httpx2.MockTransport(handler),
    ) as client:
        leave_types = await client.list_leave_types("leave-key")
        requests = await client.list_leave_requests("leave-key", employee_id=7)
        request = await client.get_leave_request("leave-key", 1)
        created = await client.create_leave_request(
            "leave-key",
            {
                "fk_user": 7,
                "fk_type": 2,
                "date_debut": 1_786_665_600,
                "date_fin": 1_786_752_000,
                "halfday": 0,
            },
        )
        updated = await client.update_leave_request(
            "leave-key",
            1,
            {"description": "Updated"},
        )
        submitted = await client.submit_leave_request("leave-key", 1)
        approved = await client.approve_leave_request("leave-key", 1)
        canceled = await client.cancel_leave_request("leave-key", 1)
        refused = await client.refuse_leave_request("leave-key", 1, "No capacity")
        reopened = await client.reopen_leave_request("leave-key", 1)

    assert leave_types[0].affects_balance is True
    assert requests[0].employee_id == 7
    assert request.request_id == 1
    assert created == 6
    assert updated.description is None
    assert submitted.status_code == 2
    assert approved.status_code == 3
    assert canceled.status_code == 4
    assert refused.status_code == 5
    assert reopened.status_code == 2
    assert all(request.headers["DOLAPIKEY"] == "leave-key" for request in seen)
    assert all("Authorization" not in request.headers for request in seen)
    assert all("sqlfilters" not in request.url.params for request in seen)
    list_request = next(
        request
        for request in seen
        if request.method == "GET" and request.url.path.endswith("/holidays")
    )
    assert list_request.url.params["user_ids"] == "7"
    assert "status,statut" in list_request.url.params["properties"]
    transition_requests = [
        request
        for request in seen
        if request.method == "POST" and "/holidays/1/" in request.url.path
    ]
    assert [request.url.path.rsplit("/", 1)[-1] for request in transition_requests] == [
        "validate",
        "approve",
        "cancel",
        "refuse",
        "reopen",
    ]
    assert transition_requests[0].content == b'{"notrigger":0}'
    assert transition_requests[3].content == (b'{"notrigger":0,"detail_refuse":"No capacity"}')


async def test_leave_client_transition_guards_and_bounds(
    settings: Settings,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(client_module, "_MAX_LEAVE_REQUESTS", 100)
    monkeypatch.setattr(client_module, "_MAX_LEAVE_TYPES", 100)

    def handler(request: httpx2.Request) -> httpx2.Response:
        if request.url.path.endswith("/holiday_types"):
            return httpx2.Response(
                200,
                json=[
                    {"id": index + 1, "code": f"T{index}", "label": f"Type {index}"}
                    for index in range(100)
                ],
            )
        return httpx2.Response(
            200,
            json=[
                {
                    "id": index + 1,
                    "fk_user": 7,
                    "fk_type": 2,
                    "date_debut": 1_786_665_600,
                    "date_fin": 1_786_752_000,
                    "status": 1,
                }
                for index in range(100)
            ],
        )

    async with DolibarrClient(settings, transport=httpx2.MockTransport(handler)) as client:
        with pytest.raises(DolibarrResultLimitError):
            await client.list_leave_types("key")
        with pytest.raises(DolibarrResultLimitError):
            await client.list_leave_requests("key")
        with pytest.raises(RuntimeError):
            await client._transition_leave_request("key", 1, "refuse")
        with pytest.raises(RuntimeError):
            await client._transition_leave_request(
                "key",
                1,
                "validate",
                refusal_reason="not allowed",
            )
