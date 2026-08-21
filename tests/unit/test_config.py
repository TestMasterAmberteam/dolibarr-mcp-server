"""Configuration and fixed upstream URL tests."""

from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from dolibarr_mcp.config import Settings


def make_settings(base_url: str, **overrides: object) -> Settings:
    values: dict[str, object] = {"DOLIBARR_BASE_URL": base_url, **overrides}
    return Settings.model_validate(values)


def test_https_base_url_and_subdirectory_are_preserved() -> None:
    settings = make_settings("https://erp.example.org/dolibarr/")
    assert settings.dolibarr_base_url == "https://erp.example.org/dolibarr"
    assert settings.api_base_url == "https://erp.example.org/dolibarr/api/index.php"
    assert settings.users_info_url == "https://erp.example.org/dolibarr/api/index.php/users/info"


@pytest.mark.parametrize(
    "url",
    [
        "ftp://erp.example.org",
        "https://user:password@erp.example.org",
        "https://erp.example.org?tenant=1",
        "https://erp.example.org#fragment",
        "https://erp.example.org:invalid",
        "https://",
        "",
        "https://erp.example.org/has space",
    ],
)
def test_invalid_base_urls_are_rejected(url: str) -> None:
    with pytest.raises(ValidationError):
        make_settings(url)


@pytest.mark.parametrize(
    "url",
    [
        "http://erp.example.org",
        "http://localhost",
        "http://127.0.0.1:8080",
        "http://[::1]:8080",
    ],
)
def test_http_is_rejected_by_default(url: str) -> None:
    with pytest.raises(ValidationError):
        make_settings(url)


@pytest.mark.parametrize(
    "url",
    ["http://localhost", "http://127.0.0.1:8080", "http://[::1]:8080/dolibarr"],
)
def test_explicit_development_flag_allows_only_loopback_http(url: str) -> None:
    settings = make_settings(url, ALLOW_INSECURE_LOCALHOST=True)
    assert settings.dolibarr_base_url.startswith("http://")


def test_development_flag_does_not_allow_remote_http() -> None:
    with pytest.raises(ValidationError):
        make_settings("http://erp.example.org", ALLOW_INSECURE_LOCALHOST=True)


def test_ca_bundle_must_exist(tmp_path: Path) -> None:
    with pytest.raises(ValidationError):
        make_settings("https://erp.example.org", DOLIBARR_CA_BUNDLE=tmp_path / "missing.pem")


def test_ca_bundle_file_is_accepted(tmp_path: Path) -> None:
    ca_bundle = tmp_path / "ca.pem"
    ca_bundle.write_text("test", encoding="utf-8")
    settings = make_settings("https://erp.example.org", DOLIBARR_CA_BUNDLE=ca_bundle)
    assert settings.dolibarr_ca_bundle == ca_bundle


def test_keepalive_limit_cannot_exceed_total_connections() -> None:
    with pytest.raises(ValidationError):
        make_settings(
            "https://erp.example.org",
            DOLIBARR_MAX_CONNECTIONS=5,
            DOLIBARR_MAX_KEEPALIVE_CONNECTIONS=6,
        )


@pytest.mark.parametrize(
    ("name", "value"),
    [
        ("MCP_ALLOWED_HOSTS", "*"),
        ("MCP_ALLOWED_HOSTS", "example.org:*"),
        ("MCP_ALLOWED_ORIGINS", "https://example.org:*"),
    ],
)
def test_production_wildcards_are_rejected(name: str, value: str) -> None:
    with pytest.raises(ValidationError):
        make_settings("https://erp.example.org", **{name: value})


def test_explicit_hosts_and_origins_are_parsed() -> None:
    settings = make_settings(
        "https://erp.example.org",
        MCP_ALLOWED_HOSTS="mcp.example.org,mcp.example.org:8443",
        MCP_ALLOWED_ORIGINS="https://console.example.org",
    )
    assert settings.allowed_hosts == ["mcp.example.org", "mcp.example.org:8443"]
    assert settings.allowed_origins == ["https://console.example.org"]
