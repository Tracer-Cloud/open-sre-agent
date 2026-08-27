"""Hermes local-log setup and verification contracts."""

from __future__ import annotations

from pathlib import Path

import pytest

from config.constants.hermes import HERMES_LOG_PATH_ENV
from core.llm.types import ToolCall
from core.tool.contracts import REGISTERED_TOOL_ATTR, RegisteredTool
from core.tool.execution import availability_view, execute_tool_calls
from integrations._catalog_impl import classify_integrations, load_env_integrations
from integrations.catalog import resolve_effective_integrations
from integrations.hermes.setup import HERMES_SETUP
from integrations.hermes.tools.hermes_logs_tool import get_hermes_logs
from integrations.hermes.verifier import verify_hermes


def test_setup_persists_the_log_path_to_its_environment_variable() -> None:
    field = HERMES_SETUP.fields[0]

    assert field.name == "log_path"
    assert field.env_var == HERMES_LOG_PATH_ENV
    assert field.default


def test_verifier_accepts_a_readable_log_file(tmp_path: Path) -> None:
    log_path = tmp_path / "errors.log"
    log_path.write_text("2026-08-27 00:00:00,000 ERROR hermes: failed\n", encoding="utf-8")

    verification = verify_hermes("setup", {"log_path": str(log_path)})

    assert verification["status"] == "passed"
    assert str(log_path) in verification["detail"]


def test_verifier_rejects_a_missing_log_file(tmp_path: Path) -> None:
    verification = verify_hermes("setup", {"log_path": str(tmp_path / "missing.log")})

    assert verification["status"] == "failed"
    assert "not found" in verification["detail"]


def test_verifier_rejects_a_directory(tmp_path: Path) -> None:
    verification = verify_hermes("setup", {"log_path": str(tmp_path)})

    assert verification["status"] == "failed"
    assert "not a file" in verification["detail"]


def test_environment_path_resolves_as_an_effective_integration(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    log_path = tmp_path / "errors.log"
    log_path.touch()
    monkeypatch.setenv(HERMES_LOG_PATH_ENV, str(log_path))

    effective = resolve_effective_integrations(store_integrations=[])
    runtime_sources = classify_integrations(load_env_integrations())
    registered = getattr(get_hermes_logs, REGISTERED_TOOL_ATTR)

    assert effective["hermes"]["source"] == "local env"
    assert effective["hermes"]["config"]["log_path"] == str(log_path)
    assert isinstance(registered, RegisteredTool)
    assert registered.is_available(availability_view(runtime_sources)) is True


def test_environment_directory_does_not_expose_the_log_tool(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv(HERMES_LOG_PATH_ENV, str(tmp_path))

    runtime_sources = classify_integrations(load_env_integrations())
    registered = getattr(get_hermes_logs, REGISTERED_TOOL_ATTR)

    assert isinstance(registered, RegisteredTool)
    assert registered.is_available(availability_view(runtime_sources)) is False


def test_runtime_uses_the_catalog_path_when_the_environment_differs(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    configured_path = tmp_path / "stored" / "errors.log"
    configured_path.parent.mkdir()
    configured_path.write_text(
        "2026-08-27 00:00:00,000 ERROR hermes: configured path\n",
        encoding="utf-8",
    )
    monkeypatch.setenv(HERMES_LOG_PATH_ENV, str(tmp_path / "env" / "errors.log"))
    runtime_sources = classify_integrations(
        [
            {
                "id": "stored-hermes",
                "service": "hermes",
                "status": "active",
                "credentials": {"log_path": str(configured_path)},
            }
        ]
    )
    registered = getattr(get_hermes_logs, REGISTERED_TOOL_ATTR)
    assert isinstance(registered, RegisteredTool)

    execution = execute_tool_calls(
        [
            ToolCall(
                id="hermes-scan",
                name="get_hermes_logs",
                input={"op": "scan", "tail_lines": 10},
            )
        ],
        [registered],
        runtime_sources,
    )[0]

    assert execution.is_error is False
    assert execution.details["records"][0]["message"] == "configured path"
