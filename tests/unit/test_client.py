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
    DolibarrNotFoundError,
    DolibarrPermissionDeniedError,
    DolibarrRateLimitedError,
    DolibarrResultLimitError,
    DolibarrUnavailableError,
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
    assert users_request.url.params["properties"] == "id,login,firstname,lastname"


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
