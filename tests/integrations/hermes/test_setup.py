"""Hermes local-log setup and verification contracts."""

from __future__ import annotations

from pathlib import Path

from config.constants.hermes import HERMES_LOG_PATH_ENV
from integrations.hermes.setup import HERMES_SETUP
from integrations.hermes.verifier import verify_hermes


def test_setup_persists_the_log_path_to_its_environment_variable() -> None:
    field = HERMES_SETUP.fields[0]

    assert field.name == "log_path"
    assert field.env_var == HERMES_LOG_PATH_ENV
    assert field.default.endswith("/.hermes/logs/errors.log")


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
