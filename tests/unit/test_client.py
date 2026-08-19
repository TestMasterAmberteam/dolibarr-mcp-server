"""Dolibarr client isolation, mapping, and payload tests."""

from __future__ import annotations

import ssl
from pathlib import Path

import httpx2
import pytest

from dolibarr_mcp.client import DolibarrClient, validated_retry_after
from dolibarr_mcp.config import Settings
from dolibarr_mcp.errors import (
    DolibarrRateLimitedError,
    DolibarrUnavailableError,
    InvalidDolibarrCredentialsError,
    InvalidDolibarrResponseError,
)

pytestmark = pytest.mark.anyio


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
