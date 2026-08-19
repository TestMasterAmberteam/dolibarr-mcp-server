"""Typed startup configuration with secure defaults."""

from __future__ import annotations

import ipaddress
from pathlib import Path  # noqa: TC003 - Pydantic resolves this field type at runtime
from typing import Literal, Self
from urllib.parse import SplitResult, urlsplit, urlunsplit

from pydantic import Field, PositiveFloat, PositiveInt, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

LOCAL_HOSTS = frozenset({"localhost", "127.0.0.1", "::1"})
DEFAULT_ALLOWED_HOSTS = "127.0.0.1,127.0.0.1:*,localhost,localhost:*,[::1],[::1]:*"


def _is_loopback(hostname: str | None) -> bool:
    if hostname is None:
        return False
    if hostname.casefold() == "localhost":
        return True
    try:
        return ipaddress.ip_address(hostname).is_loopback
    except ValueError:
        return False


def _validate_base_url(value: str, *, allow_insecure_localhost: bool) -> str:
    if not value or any(character.isspace() for character in value):
        msg = "DOLIBARR_BASE_URL must be a non-empty URL without whitespace"
        raise ValueError(msg)
    parsed = urlsplit(value)
    if parsed.scheme not in {"http", "https"}:
        msg = "DOLIBARR_BASE_URL must use https"
        raise ValueError(msg)
    if parsed.scheme == "http" and not (allow_insecure_localhost and _is_loopback(parsed.hostname)):
        msg = "http is allowed only for localhost with ALLOW_INSECURE_LOCALHOST=true"
        raise ValueError(msg)
    if parsed.username is not None or parsed.password is not None:
        msg = "DOLIBARR_BASE_URL must not contain credentials"
        raise ValueError(msg)
    if not parsed.hostname:
        msg = "DOLIBARR_BASE_URL must contain a host"
        raise ValueError(msg)
    if parsed.query or parsed.fragment:
        msg = "DOLIBARR_BASE_URL must not contain a query string or fragment"
        raise ValueError(msg)
    try:
        _ = parsed.port
    except ValueError as exc:
        msg = "DOLIBARR_BASE_URL contains an invalid port"
        raise ValueError(msg) from exc
    normalized_path = parsed.path.rstrip("/")
    return urlunsplit(SplitResult(parsed.scheme, parsed.netloc, normalized_path, "", ""))


def _split_csv(value: str, *, setting_name: str) -> list[str]:
    entries = [entry.strip() for entry in value.split(",") if entry.strip()]
    if any("*" in entry and not _is_local_port_wildcard(entry) for entry in entries):
        msg = f"{setting_name} permits :* only for explicit localhost development entries"
        raise ValueError(msg)
    return entries


def _is_local_port_wildcard(value: str) -> bool:
    if not value.endswith(":*"):
        return False
    base = value[:-2]
    if "://" in base:
        parsed = urlsplit(base)
        return parsed.scheme in {"http", "https"} and _is_loopback(parsed.hostname)
    return base.casefold() in {"localhost", "127.0.0.1", "[::1]"}


class Settings(BaseSettings):
    """Operator-controlled settings. User API keys are intentionally absent."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=True,
        extra="ignore",
        validate_default=True,
    )

    dolibarr_base_url: str = Field(alias="DOLIBARR_BASE_URL")
    dolibarr_ca_bundle: Path | None = Field(default=None, alias="DOLIBARR_CA_BUNDLE")
    host: str = Field(default="127.0.0.1", alias="HOST", min_length=1)
    port: int = Field(default=8000, alias="PORT", ge=1, le=65535)
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"] = Field(
        default="INFO", alias="LOG_LEVEL"
    )
    mcp_allowed_hosts: str = Field(default=DEFAULT_ALLOWED_HOSTS, alias="MCP_ALLOWED_HOSTS")
    mcp_allowed_origins: str = Field(default="", alias="MCP_ALLOWED_ORIGINS")
    dolibarr_connect_timeout: PositiveFloat = Field(default=5.0, alias="DOLIBARR_CONNECT_TIMEOUT")
    dolibarr_read_timeout: PositiveFloat = Field(default=10.0, alias="DOLIBARR_READ_TIMEOUT")
    dolibarr_write_timeout: PositiveFloat = Field(default=10.0, alias="DOLIBARR_WRITE_TIMEOUT")
    dolibarr_pool_timeout: PositiveFloat = Field(default=5.0, alias="DOLIBARR_POOL_TIMEOUT")
    dolibarr_max_connections: PositiveInt = Field(default=100, alias="DOLIBARR_MAX_CONNECTIONS")
    dolibarr_max_keepalive_connections: PositiveInt = Field(
        default=20, alias="DOLIBARR_MAX_KEEPALIVE_CONNECTIONS"
    )
    allow_insecure_localhost: bool = Field(default=False, alias="ALLOW_INSECURE_LOCALHOST")

    @model_validator(mode="after")
    def validate_security_settings(self) -> Self:
        """Validate cross-field URL, CA, pool, host, and origin invariants."""
        self.dolibarr_base_url = _validate_base_url(
            self.dolibarr_base_url,
            allow_insecure_localhost=self.allow_insecure_localhost,
        )
        if self.dolibarr_ca_bundle is not None and not self.dolibarr_ca_bundle.is_file():
            msg = "DOLIBARR_CA_BUNDLE must point to a readable file"
            raise ValueError(msg)
        if self.dolibarr_max_keepalive_connections > self.dolibarr_max_connections:
            msg = "DOLIBARR_MAX_KEEPALIVE_CONNECTIONS cannot exceed DOLIBARR_MAX_CONNECTIONS"
            raise ValueError(msg)
        _split_csv(self.mcp_allowed_hosts, setting_name="MCP_ALLOWED_HOSTS")
        _split_csv(self.mcp_allowed_origins, setting_name="MCP_ALLOWED_ORIGINS")
        if not self.allowed_hosts:
            msg = "MCP_ALLOWED_HOSTS must contain at least one explicit host"
            raise ValueError(msg)
        return self

    @property
    def allowed_hosts(self) -> list[str]:
        """Return validated DNS-rebinding Host allowlist entries."""
        return _split_csv(self.mcp_allowed_hosts, setting_name="MCP_ALLOWED_HOSTS")

    @property
    def allowed_origins(self) -> list[str]:
        """Return validated Origin allowlist entries; empty disables browser origins."""
        return _split_csv(self.mcp_allowed_origins, setting_name="MCP_ALLOWED_ORIGINS")

    @property
    def users_info_url(self) -> str:
        """Build the fixed identity endpoint while retaining a Dolibarr subdirectory."""
        return f"{self.dolibarr_base_url}/api/index.php/users/info"
