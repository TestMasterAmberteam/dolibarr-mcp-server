"""CLI behavior and startup wiring tests."""

from __future__ import annotations

import builtins
from collections.abc import Mapping, Sequence
from pathlib import Path
from types import ModuleType

import pytest

from dolibarr_mcp.__main__ import main
from dolibarr_mcp.app import build_app_from_environment


def test_help_does_not_require_server_configuration(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as captured:
        main(["--help"])
    assert captured.value.code == 0
    assert "Streamable HTTP" in capsys.readouterr().out


def test_help_does_not_import_server_dependencies(
    capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    original_import = builtins.__import__

    def guarded_import(
        name: str,
        globals_: Mapping[str, object] | None = None,
        locals_: Mapping[str, object] | None = None,
        fromlist: Sequence[str] | None = (),
        level: int = 0,
    ) -> ModuleType:
        blocked = (
            "uvicorn",
            "pydantic",
            "dolibarr_mcp.app",
            "dolibarr_mcp.config",
            "dolibarr_mcp.logging",
        )
        if name == blocked[0] or name == blocked[1] or name.startswith(blocked[2:]):
            message = f"unexpected server dependency import: {name}"
            raise AssertionError(message)
        return original_import(name, globals_, locals_, fromlist, level)

    monkeypatch.setattr(builtins, "__import__", guarded_import)
    with pytest.raises(SystemExit) as captured:
        main(["--help"])
    assert captured.value.code == 0
    assert "Streamable HTTP" in capsys.readouterr().out


def test_version_does_not_require_server_configuration(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as captured:
        main(["--version"])
    assert captured.value.code == 0
    assert capsys.readouterr().out.strip() == "dolibarr-mcp 0.2.0"


def test_missing_required_configuration_exits_safely(
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.chdir(tmp_path)
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

    monkeypatch.setattr("uvicorn.run", fake_run)
    monkeypatch.setattr("dolibarr_mcp.logging.configure_logging", lambda _level: None)
    assert main([]) == 0
    assert calls[0]["host"] == "127.0.0.1"
    assert calls[0]["port"] == 8000
    assert calls[0]["access_log"] is False
    assert calls[0]["proxy_headers"] is False


def test_environment_app_factory(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DOLIBARR_BASE_URL", "http://localhost:18080/dolibarr")
    monkeypatch.setenv("ALLOW_INSECURE_LOCALHOST", "true")
    assert build_app_from_environment().state.ready is False
