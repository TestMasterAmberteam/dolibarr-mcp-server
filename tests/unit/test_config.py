"""Configuration and fixed upstream URL tests."""

from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from dolibarr_mcp.config import ConfigurationFileError, Settings, load_settings

EXAMPLE_CONFIG = Path(__file__).parents[2] / "config.example.toml"


def make_settings(base_url: str, **overrides: object) -> Settings:
    values: dict[str, object] = {"dolibarr_base_url": base_url, **overrides}
    return Settings.model_validate(values)


def stage_values(
    stage_id: int,
    *,
    label: str = "P3L - Lost",
    aliases: list[str] | None = None,
    percent: float = 0,
    position: int = 70,
    active: bool = True,
) -> dict[str, object]:
    return {
        "id": stage_id,
        "label": label,
        "aliases": aliases or [],
        "percent": percent,
        "position": position,
        "active": active,
    }


def test_https_base_url_and_subdirectory_are_preserved() -> None:
    settings = make_settings("https://erp.example.org/dolibarr/")
    assert settings.dolibarr_base_url == "https://erp.example.org/dolibarr"
    assert settings.api_base_url == "https://erp.example.org/dolibarr/api/index.php"
    assert settings.users_info_url == "https://erp.example.org/dolibarr/api/index.php/users/info"


def test_shipped_example_config_is_valid() -> None:
    settings = load_settings(EXAMPLE_CONFIG)

    assert settings.dolibarr_base_url == "https://erp.example.invalid/dolibarr"
    assert settings.dolibarr_lead_stage_catalog == {}


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
    settings = make_settings(url, allow_insecure_localhost=True)
    assert settings.dolibarr_base_url.startswith("http://")


def test_development_flag_does_not_allow_remote_http() -> None:
    with pytest.raises(ValidationError):
        make_settings("http://erp.example.org", allow_insecure_localhost=True)


def test_ca_bundle_must_exist(tmp_path: Path) -> None:
    with pytest.raises(ValidationError):
        make_settings("https://erp.example.org", dolibarr_ca_bundle=tmp_path / "missing.pem")


def test_ca_bundle_file_is_accepted(tmp_path: Path) -> None:
    ca_bundle = tmp_path / "ca.pem"
    ca_bundle.write_text("test", encoding="utf-8")
    settings = make_settings("https://erp.example.org", dolibarr_ca_bundle=ca_bundle)
    assert settings.dolibarr_ca_bundle == ca_bundle


def test_keepalive_limit_cannot_exceed_total_connections() -> None:
    with pytest.raises(ValidationError):
        make_settings(
            "https://erp.example.org",
            dolibarr_max_connections=5,
            dolibarr_max_keepalive_connections=6,
        )


@pytest.mark.parametrize(
    ("name", "value"),
    [
        ("mcp_allowed_hosts", ["*"]),
        ("mcp_allowed_hosts", ["example.org:*"]),
        ("mcp_allowed_origins", ["https://example.org:*"]),
    ],
)
def test_production_wildcards_are_rejected(name: str, value: list[str]) -> None:
    with pytest.raises(ValidationError):
        make_settings("https://erp.example.org", **{name: value})


def test_explicit_hosts_and_origins_are_parsed() -> None:
    settings = make_settings(
        "https://erp.example.org",
        mcp_allowed_hosts=["mcp.example.org", "mcp.example.org:8443"],
        mcp_allowed_origins=["https://console.example.org"],
    )
    assert settings.allowed_hosts == ["mcp.example.org", "mcp.example.org:8443"]
    assert settings.allowed_origins == ["https://console.example.org"]


def test_operator_lead_stage_catalog_is_normalized_and_typed() -> None:
    settings = make_settings(
        "https://erp.example.org",
        dolibarr_lead_stage_catalog={
            " LOST ": stage_values(7, aliases=[" P3L "]),
            "WON": stage_values(
                6,
                label="P3W - Won",
                aliases=["P3W"],
                percent=100,
                position=60,
            ),
        },
    )

    assert list(settings.dolibarr_lead_stage_catalog) == ["LOST", "WON"]
    assert settings.dolibarr_lead_stage_catalog["LOST"].id == 7
    assert settings.dolibarr_lead_stage_catalog["LOST"].aliases == ["P3L"]
    assert settings.dolibarr_lead_stage_catalog["WON"].percent == 100


def test_settings_are_loaded_from_toml_without_environment_override(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    config_path = tmp_path / "config.toml"
    config_path.write_text(
        'dolibarr_base_url = "https://erp.example.org/dolibarr"\n'
        'mcp_allowed_hosts = ["mcp.example.org"]\n'
        "[dolibarr_lead_stage_catalog.LOST]\n"
        "id = 7\n"
        'label = "P3L - Lost"\n'
        'aliases = ["P3L"]\n'
        "percent = 0\n"
        "position = 70\n"
        "active = true\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("DOLIBARR_BASE_URL", "https://ignored.example.org")
    monkeypatch.setenv("DOLIBARR_LEAD_STAGE_CATALOG", '{"WRONG":99}')

    settings = load_settings(config_path)

    assert settings.dolibarr_base_url == "https://erp.example.org/dolibarr"
    assert settings.dolibarr_lead_stage_catalog["LOST"].id == 7
    assert settings.dolibarr_lead_stage_catalog["LOST"].aliases == ["P3L"]


@pytest.mark.parametrize(
    "catalog",
    [
        {"": stage_values(7)},
        {"P3 L": stage_values(7)},
        {"LOST": stage_values(7), "lost": stage_values(8)},
        {"LOST": stage_values(7), "WON": stage_values(7)},
        {"LOST": stage_values(7, aliases=["LOST"])},
        {
            "LOST": stage_values(7, aliases=["P3L"]),
            "P3L": stage_values(8),
        },
        {"LOST": stage_values(7, aliases=["P3 L"])},
        {f"S{index}": stage_values(index + 1) for index in range(101)},
    ],
)
def test_invalid_operator_lead_stage_catalog_is_rejected(
    catalog: dict[str, dict[str, object]],
) -> None:
    with pytest.raises(ValidationError):
        make_settings(
            "https://erp.example.org",
            dolibarr_lead_stage_catalog=catalog,
        )


def test_relative_ca_bundle_is_resolved_from_config_directory(tmp_path: Path) -> None:
    ca_bundle = tmp_path / "private-ca.pem"
    ca_bundle.write_text("test", encoding="utf-8")
    config_path = tmp_path / "config.toml"
    config_path.write_text(
        'dolibarr_base_url = "https://erp.example.org"\ndolibarr_ca_bundle = "private-ca.pem"\n',
        encoding="utf-8",
    )

    assert load_settings(config_path).dolibarr_ca_bundle == ca_bundle


@pytest.mark.parametrize("contents", ["not = [valid", "unknown_setting = true"])
def test_invalid_toml_file_is_rejected(tmp_path: Path, contents: str) -> None:
    config_path = tmp_path / "config.toml"
    config_path.write_text(contents, encoding="utf-8")

    expected_error = (ConfigurationFileError, ValidationError)
    with pytest.raises(expected_error):
        load_settings(config_path)


def test_missing_toml_file_is_rejected(tmp_path: Path) -> None:
    with pytest.raises(ConfigurationFileError):
        load_settings(tmp_path / "missing.toml")
