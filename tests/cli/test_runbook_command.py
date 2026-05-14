from __future__ import annotations

from pathlib import Path

import pytest
from click.testing import CliRunner

from app.cli.__main__ import cli


def _patch_home(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    home = tmp_path / "opensre_home"
    monkeypatch.setattr("app.constants.OPENSRE_HOME_DIR", home)
    return home


def _make_fixture(path: Path, *, with_triggers: bool = True) -> Path:
    frontmatter = "service: payments-api\n"
    if with_triggers:
        frontmatter += "triggers:\n  - oom\n  - memory\n"
    path.write_text(f"---\n{frontmatter}---\n# body\n", encoding="utf-8")
    return path


def test_runbook_add_writes_to_store(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    home = _patch_home(monkeypatch, tmp_path)
    source = _make_fixture(tmp_path / "payments-oom.md")

    runner = CliRunner()
    result = runner.invoke(cli, ["runbook", "add", str(source)])

    assert result.exit_code == 0, result.output
    assert "Saved runbook 'payments-oom'" in result.output
    assert (home / "runbooks" / "payments-oom.md").exists()


def test_runbook_add_rejects_invalid_frontmatter(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _patch_home(monkeypatch, tmp_path)
    source = _make_fixture(tmp_path / "bad.md", with_triggers=False)

    runner = CliRunner()
    result = runner.invoke(cli, ["runbook", "add", str(source)])

    assert result.exit_code != 0
    assert "triggers" in result.output


def test_runbook_list_shows_entries(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    home = _patch_home(monkeypatch, tmp_path)
    target = home / "runbooks" / "payments-oom.md"
    target.parent.mkdir(parents=True, exist_ok=True)
    _make_fixture(target)

    runner = CliRunner()
    result = runner.invoke(cli, ["runbook", "list"])

    assert result.exit_code == 0
    assert "payments-oom" in result.output
    assert "service=payments-api" in result.output
    assert "oom" in result.output


def test_runbook_list_empty_message(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    _patch_home(monkeypatch, tmp_path)

    runner = CliRunner()
    result = runner.invoke(cli, ["runbook", "list"])

    assert result.exit_code == 0
    assert "No runbooks found" in result.output


def test_runbook_remove_deletes_file(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    home = _patch_home(monkeypatch, tmp_path)
    target = home / "runbooks" / "payments-oom.md"
    target.parent.mkdir(parents=True, exist_ok=True)
    _make_fixture(target)

    runner = CliRunner()
    result = runner.invoke(cli, ["runbook", "remove", "payments-oom"])

    assert result.exit_code == 0
    assert "Removed runbook 'payments-oom'" in result.output
    assert not target.exists()


def test_runbook_remove_unknown_slug_errors(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _patch_home(monkeypatch, tmp_path)

    runner = CliRunner()
    result = runner.invoke(cli, ["runbook", "remove", "missing"])

    assert result.exit_code != 0
    assert "no runbook" in result.output
