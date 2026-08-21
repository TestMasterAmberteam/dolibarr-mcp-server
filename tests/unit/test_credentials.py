"""Request credential lifecycle and async-context invalidation tests."""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

import httpx2
import pytest

from dolibarr_mcp.credentials import (
    RequestCredential,
    RequestCredentialContextMiddleware,
    get_request_api_key,
    store_request_api_key,
)

if TYPE_CHECKING:
    from starlette.types import Receive, Scope, Send

pytestmark = pytest.mark.anyio


def test_credential_holder_store_get_clear_and_duplicate_rejection() -> None:
    credential = RequestCredential()
    with pytest.raises(PermissionError):
        credential.get()
    credential.store("secret")
    assert credential.get() == "secret"
    with pytest.raises(RuntimeError):
        credential.store("second")
    credential.clear()
    with pytest.raises(PermissionError):
        credential.get()
    assert "secret" not in repr(credential)


async def test_middleware_clears_copied_child_context_after_response() -> None:
    release_child = asyncio.Event()
    child_result: asyncio.Task[str] | None = None

    async def child() -> str:
        await release_child.wait()
        with pytest.raises(PermissionError):
            return get_request_api_key()
        return "cleared"

    async def app(
        _scope: Scope,
        _receive: Receive,
        send: Send,
    ) -> None:
        nonlocal child_result
        store_request_api_key("secret")
        assert get_request_api_key() == "secret"
        child_result = asyncio.create_task(child())
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b"ok"})

    middleware = RequestCredentialContextMiddleware(app)
    async with httpx2.AsyncClient(
        transport=httpx2.ASGITransport(app=middleware),
        base_url="http://localhost",
    ) as client:
        response = await client.get("/")
    assert response.status_code == 200
    release_child.set()
    assert child_result is not None
    assert await child_result == "cleared"
    with pytest.raises(PermissionError):
        get_request_api_key()


def test_context_functions_fail_outside_middleware() -> None:
    with pytest.raises(RuntimeError):
        store_request_api_key("secret")
    with pytest.raises(PermissionError):
        get_request_api_key()
