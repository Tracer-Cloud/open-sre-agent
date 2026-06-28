from __future__ import annotations

import os
from pathlib import Path

import pytest

from config.env_loading import load_opensre_env_files


def test_load_opensre_env_files_prefers_project_over_home(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    home = tmp_path / "home"
    project = tmp_path / "project"
    home.mkdir()
    project.mkdir()
    (home / ".env").write_text("LLM_PROVIDER=anthropic\n", encoding="utf-8")
    env_file = project / ".env"
    env_file.write_text("LLM_PROVIDER=openai\n", encoding="utf-8")

    monkeypatch.setattr("config.env_loading.OPENSRE_HOME_DIR", home)
    monkeypatch.setenv("OPENSRE_PROJECT_ENV_PATH", str(env_file))
    monkeypatch.delenv("LLM_PROVIDER", raising=False)

    load_opensre_env_files()

    assert os.environ["LLM_PROVIDER"] == "openai"


def test_load_opensre_env_files_keeps_shell_exports_over_project_file(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    home = tmp_path / "home"
    project = tmp_path / "project"
    home.mkdir()
    project.mkdir()
    (home / ".env").write_text("LLM_PROVIDER=anthropic\n", encoding="utf-8")
    (project / ".env").write_text("LLM_PROVIDER=openai\n", encoding="utf-8")

    monkeypatch.setattr("config.env_loading.OPENSRE_HOME_DIR", home)
    monkeypatch.delenv("OPENSRE_PROJECT_ENV_PATH", raising=False)
    monkeypatch.setenv("LLM_PROVIDER", "shell-export")
    monkeypatch.chdir(project)

    load_opensre_env_files()

    assert os.environ["LLM_PROVIDER"] == "shell-export"


def test_load_opensre_env_files_loads_home_only_when_no_project_env(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    home = tmp_path / "home"
    home.mkdir()
    (home / ".env").write_text("LLM_PROVIDER=anthropic\n", encoding="utf-8")

    monkeypatch.setattr("config.env_loading.OPENSRE_HOME_DIR", home)
    monkeypatch.delenv("OPENSRE_PROJECT_ENV_PATH", raising=False)
    monkeypatch.delenv("LLM_PROVIDER", raising=False)

    load_opensre_env_files()

    assert os.environ["LLM_PROVIDER"] == "anthropic"


def test_load_opensre_env_files_ignores_home_project_env_path_redirect(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Home ``.env`` must not override a shell-exported project env path."""
    home = tmp_path / "home"
    project = tmp_path / "project"
    decoy = tmp_path / "decoy.env"
    home.mkdir()
    project.mkdir()
    project_env = project / ".env"
    (home / ".env").write_text(
        f"OPENSRE_PROJECT_ENV_PATH={decoy}\nLLM_PROVIDER=anthropic\n",
        encoding="utf-8",
    )
    project_env.write_text("LLM_PROVIDER=openai\n", encoding="utf-8")
    decoy.write_text("LLM_PROVIDER=decoy\n", encoding="utf-8")

    monkeypatch.setattr("config.env_loading.OPENSRE_HOME_DIR", home)
    monkeypatch.setenv("OPENSRE_PROJECT_ENV_PATH", str(project_env))
    monkeypatch.delenv("LLM_PROVIDER", raising=False)

    load_opensre_env_files()

    assert os.environ["LLM_PROVIDER"] == "openai"
