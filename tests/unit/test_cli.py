"""CLI behavior and startup wiring tests."""

from __future__ import annotations

import pytest

from dolibarr_mcp.__main__ import main
from dolibarr_mcp.app import build_app_from_environment


def test_help_does_not_require_server_configuration(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as captured:
        main(["--help"])
    assert captured.value.code == 0
    assert "Streamable HTTP" in capsys.readouterr().out


def test_version_does_not_require_server_configuration(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as captured:
        main(["--version"])
    assert captured.value.code == 0
    assert capsys.readouterr().out.strip() == "dolibarr-mcp 0.1.0"


def test_missing_required_configuration_exits_safely(
    capsys: pytest.CaptureFixture[str],
) -> None:
    with pytest.raises(SystemExit) as captured:
        main([])
    assert captured.value.code == 2
    error = capsys.readouterr().err
    assert "invalid configuration" in error
    assert "DOLIBARR_BASE_URL" not in error


def test_valid_configuration_starts_uvicorn_without_proxy_header_trust(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("DOLIBARR_BASE_URL", "http://localhost:18080/dolibarr")
    monkeypatch.setenv("ALLOW_INSECURE_LOCALHOST", "true")
    calls: list[dict[str, object]] = []

    def fake_run(app: object, **kwargs: object) -> None:
        calls.append({"app": app, **kwargs})

    monkeypatch.setattr("dolibarr_mcp.__main__.uvicorn.run", fake_run)
    monkeypatch.setattr("dolibarr_mcp.__main__.configure_logging", lambda _level: None)
    assert main([]) == 0
    assert calls[0]["host"] == "127.0.0.1"
    assert calls[0]["port"] == 8000
    assert calls[0]["access_log"] is False
    assert calls[0]["proxy_headers"] is False


def test_environment_app_factory(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DOLIBARR_BASE_URL", "http://localhost:18080/dolibarr")
    monkeypatch.setenv("ALLOW_INSECURE_LOCALHOST", "true")
    assert build_app_from_environment().state.ready is False
