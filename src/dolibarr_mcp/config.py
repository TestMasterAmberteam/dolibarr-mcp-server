"""Typed startup configuration with secure defaults."""

from __future__ import annotations

import ipaddress
import re
import tomllib
from pathlib import Path
from typing import Literal, Self
from urllib.parse import SplitResult, urlsplit, urlunsplit

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    PositiveFloat,
    PositiveInt,
    field_validator,
    model_validator,
)

LOCAL_HOSTS = frozenset({"localhost", "127.0.0.1", "::1"})
DEFAULT_ALLOWED_HOSTS = (
    "127.0.0.1",
    "127.0.0.1:*",
    "localhost",
    "localhost:*",
    "[::1]",
    "[::1]:*",
)
MAX_LEAD_STAGE_CATALOG_ENTRIES = 100
MAX_LEAD_STAGE_ALIASES = 10
_LEAD_STAGE_IDENTIFIER_PATTERN = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,63}")


class ConfigurationFileError(ValueError):
    """The selected startup configuration file cannot be read safely."""

    def __init__(self) -> None:
        super().__init__("Invalid or unreadable configuration file.")


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
        msg = "dolibarr_base_url must be a non-empty URL without whitespace"
        raise ValueError(msg)
    parsed = urlsplit(value)
    if parsed.scheme not in {"http", "https"}:
        msg = "dolibarr_base_url must use https"
        raise ValueError(msg)
    if parsed.scheme == "http" and not (allow_insecure_localhost and _is_loopback(parsed.hostname)):
        msg = "http is allowed only for localhost with allow_insecure_localhost=true"
        raise ValueError(msg)
    if parsed.username is not None or parsed.password is not None:
        msg = "dolibarr_base_url must not contain credentials"
        raise ValueError(msg)
    if not parsed.hostname:
        msg = "dolibarr_base_url must contain a host"
        raise ValueError(msg)
    if parsed.query or parsed.fragment:
        msg = "dolibarr_base_url must not contain a query string or fragment"
        raise ValueError(msg)
    try:
        _ = parsed.port
    except ValueError as exc:
        msg = "dolibarr_base_url contains an invalid port"
        raise ValueError(msg) from exc
    normalized_path = parsed.path.rstrip("/")
    return urlunsplit(SplitResult(parsed.scheme, parsed.netloc, normalized_path, "", ""))


def _normalize_allowlist(value: list[str], *, setting_name: str) -> list[str]:
    entries = [entry.strip() for entry in value if entry.strip()]
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


class LeadStageConfig(BaseModel):
    """One operator-verified Dolibarr opportunity-stage dictionary row."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    id: PositiveInt
    label: str = Field(min_length=1, max_length=255)
    aliases: list[str] = Field(default_factory=list, max_length=MAX_LEAD_STAGE_ALIASES)
    percent: float = Field(ge=0, le=100)
    position: int
    active: bool = True

    @field_validator("label")
    @classmethod
    def normalize_label(cls, value: str) -> str:
        """Reject whitespace-only labels and retain a normalized display value."""
        normalized = value.strip()
        if not normalized:
            msg = "lead-stage labels must contain non-whitespace text"
            raise ValueError(msg)
        return normalized

    @field_validator("aliases")
    @classmethod
    def normalize_aliases(cls, value: list[str]) -> list[str]:
        """Normalize and validate business aliases before catalog-wide checks."""
        normalized: list[str] = []
        seen: set[str] = set()
        for raw_alias in value:
            alias = raw_alias.strip()
            normalized_alias = alias.casefold()
            if not _LEAD_STAGE_IDENTIFIER_PATTERN.fullmatch(alias):
                msg = (
                    "lead-stage aliases must be 1-64 ASCII letters, digits, dots, "
                    "underscores, or hyphens"
                )
                raise ValueError(msg)
            if normalized_alias in seen:
                msg = "lead-stage aliases must be unique ignoring case"
                raise ValueError(msg)
            seen.add(normalized_alias)
            normalized.append(alias)
        return normalized


class Settings(BaseModel):
    """Validated non-secret settings loaded exclusively from a configuration file."""

    model_config = ConfigDict(extra="forbid", validate_default=True)

    dolibarr_base_url: str
    dolibarr_ca_bundle: Path | None = None
    host: str = Field(default="127.0.0.1", min_length=1)
    port: int = Field(default=8000, ge=1, le=65535)
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"] = Field(default="INFO")
    mcp_allowed_hosts: list[str] = Field(
        default_factory=lambda: list(DEFAULT_ALLOWED_HOSTS),
    )
    mcp_allowed_origins: list[str] = Field(default_factory=list)
    dolibarr_connect_timeout: PositiveFloat = 5.0
    dolibarr_read_timeout: PositiveFloat = 10.0
    dolibarr_write_timeout: PositiveFloat = 10.0
    dolibarr_pool_timeout: PositiveFloat = 5.0
    dolibarr_max_connections: PositiveInt = 100
    dolibarr_max_keepalive_connections: PositiveInt = 20
    dolibarr_lead_stage_catalog: dict[str, LeadStageConfig] = Field(default_factory=dict)
    allow_insecure_localhost: bool = False

    @model_validator(mode="after")
    def validate_security_settings(self) -> Self:
        """Validate cross-field URL, CA, pool, host, and origin invariants."""
        self.dolibarr_base_url = _validate_base_url(
            self.dolibarr_base_url,
            allow_insecure_localhost=self.allow_insecure_localhost,
        )
        if self.dolibarr_ca_bundle is not None and not self.dolibarr_ca_bundle.is_file():
            msg = "dolibarr_ca_bundle must point to a readable file"
            raise ValueError(msg)
        if self.dolibarr_max_keepalive_connections > self.dolibarr_max_connections:
            msg = "dolibarr_max_keepalive_connections cannot exceed dolibarr_max_connections"
            raise ValueError(msg)
        if len(self.dolibarr_lead_stage_catalog) > MAX_LEAD_STAGE_CATALOG_ENTRIES:
            msg = "dolibarr_lead_stage_catalog cannot contain more than 100 entries"
            raise ValueError(msg)
        normalized_catalog: dict[str, LeadStageConfig] = {}
        normalized_identifiers: set[str] = set()
        stage_ids: set[int] = set()
        for raw_code, stage in self.dolibarr_lead_stage_catalog.items():
            code = raw_code.strip()
            normalized_code = code.casefold()
            if not _LEAD_STAGE_IDENTIFIER_PATTERN.fullmatch(code):
                msg = (
                    "dolibarr_lead_stage_catalog codes must be 1-64 ASCII letters, "
                    "digits, dots, underscores, or hyphens"
                )
                raise ValueError(msg)
            identifiers = [normalized_code, *(alias.casefold() for alias in stage.aliases)]
            if len(set(identifiers)) != len(identifiers):
                msg = "a lead-stage alias cannot duplicate its canonical code"
                raise ValueError(msg)
            if any(identifier in normalized_identifiers for identifier in identifiers):
                msg = "lead-stage codes and aliases must be globally unique ignoring case"
                raise ValueError(msg)
            if int(stage.id) in stage_ids:
                msg = "dolibarr_lead_stage_catalog stage IDs must be unique"
                raise ValueError(msg)
            normalized_identifiers.update(identifiers)
            stage_ids.add(int(stage.id))
            normalized_catalog[code] = stage
        self.dolibarr_lead_stage_catalog = normalized_catalog
        self.mcp_allowed_hosts = _normalize_allowlist(
            self.mcp_allowed_hosts,
            setting_name="mcp_allowed_hosts",
        )
        self.mcp_allowed_origins = _normalize_allowlist(
            self.mcp_allowed_origins,
            setting_name="mcp_allowed_origins",
        )
        if not self.allowed_hosts:
            msg = "mcp_allowed_hosts must contain at least one explicit host"
            raise ValueError(msg)
        return self

    @property
    def allowed_hosts(self) -> list[str]:
        """Return validated DNS-rebinding Host allowlist entries."""
        return list(self.mcp_allowed_hosts)

    @property
    def allowed_origins(self) -> list[str]:
        """Return validated Origin allowlist entries; empty disables browser origins."""
        return list(self.mcp_allowed_origins)

    @property
    def users_info_url(self) -> str:
        """Build the fixed identity endpoint while retaining a Dolibarr subdirectory."""
        return f"{self.api_base_url}/users/info"

    @property
    def api_base_url(self) -> str:
        """Build the fixed REST API root while retaining a Dolibarr subdirectory."""
        return f"{self.dolibarr_base_url}/api/index.php"


def load_settings(path: str | Path = "config.toml") -> Settings:
    """Read and validate non-secret settings from one explicit TOML file."""
    config_path = Path(path)
    try:
        with config_path.open("rb") as stream:
            values = tomllib.load(stream)
    except (OSError, tomllib.TOMLDecodeError) as exc:
        raise ConfigurationFileError from exc
    ca_bundle = values.get("dolibarr_ca_bundle")
    if isinstance(ca_bundle, str):
        ca_path = Path(ca_bundle)
        if not ca_path.is_absolute():
            values["dolibarr_ca_bundle"] = config_path.parent / ca_path
    return Settings.model_validate(values)
