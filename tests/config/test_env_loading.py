from __future__ import annotations

from pathlib import Path

from config.env_loading import load_opensre_env_files


def test_load_opensre_env_files_prefers_project_over_home(
    monkeypatch, tmp_path: Path
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

    import os

    assert os.environ["LLM_PROVIDER"] == "openai"
