"""Shared test fixtures."""

from __future__ import annotations

import pytest

from dolibarr_mcp.config import Settings


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture
def settings() -> Settings:
    return Settings.model_validate(
        {
            "DOLIBARR_BASE_URL": "http://localhost:18080/dolibarr",
            "ALLOW_INSECURE_LOCALHOST": True,
        }
    )


@pytest.fixture(autouse=True)
def no_operator_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep local developer configuration from affecting deterministic tests."""
    names = (
        "DOLIBARR_BASE_URL",
        "DOLIBARR_CA_BUNDLE",
        "HOST",
        "PORT",
        "LOG_LEVEL",
        "MCP_ALLOWED_HOSTS",
        "MCP_ALLOWED_ORIGINS",
        "ALLOW_INSECURE_LOCALHOST",
    )
    for name in names:
        monkeypatch.delenv(name, raising=False)
