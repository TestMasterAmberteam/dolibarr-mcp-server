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
            "dolibarr_base_url": "http://localhost:18080/dolibarr",
            "allow_insecure_localhost": True,
        }
    )
