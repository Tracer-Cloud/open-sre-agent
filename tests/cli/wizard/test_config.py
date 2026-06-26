"""Regression tests for wizard path constants."""

from __future__ import annotations

from cli.wizard.config import PROJECT_ENV_PATH, PROJECT_ROOT


def test_project_env_path_defaults_to_repo_root() -> None:
    """PROJECT_ROOT must resolve to the checkout root, not its parent directory."""
    assert (PROJECT_ROOT / "pyproject.toml").is_file()
    assert PROJECT_ENV_PATH == PROJECT_ROOT / ".env"
