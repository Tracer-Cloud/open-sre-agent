"""Tests for /rca history and /rca show."""

from __future__ import annotations

import io
import json
from pathlib import Path

import pytest
from rich.console import Console

from interactive_shell.command_registry import dispatch_slash
from interactive_shell.session import JsonlSessionStorage, ReplSession

SessionStore = JsonlSessionStorage()


def _capture() -> tuple[Console, io.StringIO]:
    buf = io.StringIO()
    return Console(file=buf, force_terminal=False, highlight=False), buf


def test_rca_history_lists_persisted_reports(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("config.constants.OPENSRE_HOME_DIR", tmp_path)
    session = ReplSession()
    SessionStore.open_session(session)
    SessionStore.append_investigation_result(
        session.session_id,
        {"root_cause": "redis timeout", "problem_md": "cache unavailable"},
        trigger="/investigate generic",
    )

    console, buf = _capture()
    assert dispatch_slash("/rca history", ReplSession(), console) is True
    output = buf.getvalue()
    assert "RCA history" in output
    assert "redis timeout" in output
    assert "/investigate generic" in output


def test_rca_show_renders_full_report(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("config.constants.OPENSRE_HOME_DIR", tmp_path)
    session = ReplSession()
    SessionStore.open_session(session)
    inv_id = SessionStore.append_investigation_result(
        session.session_id,
        {"root_cause": "bad deploy", "problem_md": "## Report\nRollback required"},
        trigger="/investigate grafana",
    )

    console, buf = _capture()
    assert dispatch_slash(f"/rca show {inv_id[:4]}", ReplSession(), console) is True
    output = buf.getvalue()
    assert "bad deploy" in output
    assert "Rollback required" in output


def test_bare_rca_defaults_to_history(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("config.constants.OPENSRE_HOME_DIR", tmp_path)
    console, buf = _capture()
    assert dispatch_slash("/rca", ReplSession(), console) is True
    assert "no persisted RCA reports yet" in buf.getvalue()


def test_tty_rca_menu_latest_shows_report(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from interactive_shell.command_registry import rca_cmds

    monkeypatch.setattr("config.constants.OPENSRE_HOME_DIR", tmp_path)
    session = ReplSession()
    SessionStore.open_session(session)
    inv_id = SessionStore.append_investigation_result(
        session.session_id,
        {"root_cause": "connection pool exhausted", "problem_md": "## Report\nPool at max"},
        trigger="/investigate generic",
    )

    monkeypatch.setattr(rca_cmds, "repl_tty_interactive", lambda: True)
    monkeypatch.setattr(rca_cmds, "repl_choose_one", lambda **_: rca_cmds._RCA_LATEST)

    console, buf = _capture()
    assert dispatch_slash("/rca", session, console) is True
    output = buf.getvalue()
    assert "connection pool exhausted" in output
    assert "Pool at max" in output
    assert inv_id[:8] in output


def test_tty_rca_history_menu_picks_report_directly(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from interactive_shell.command_registry import rca_cmds

    monkeypatch.setattr("config.constants.OPENSRE_HOME_DIR", tmp_path)
    session = ReplSession()
    SessionStore.open_session(session)
    older_id = SessionStore.append_investigation_result(
        session.session_id,
        {"root_cause": "older issue", "problem_md": "older report"},
        trigger="/investigate grafana",
    )
    SessionStore.append_investigation_result(
        session.session_id,
        {"root_cause": "newer issue", "problem_md": "newer report"},
        trigger="/investigate generic",
    )

    monkeypatch.setattr(rca_cmds, "repl_tty_interactive", lambda: True)
    monkeypatch.setattr(rca_cmds, "repl_choose_one", lambda **_: older_id)

    console, buf = _capture()
    assert dispatch_slash("/rca history", session, console) is True
    output = buf.getvalue()
    assert "older issue" in output
    assert "older report" in output
    assert "newer issue" not in output


def test_tty_rca_root_menu_history_picks_report(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from interactive_shell.command_registry import rca_cmds

    monkeypatch.setattr("config.constants.OPENSRE_HOME_DIR", tmp_path)
    session = ReplSession()
    SessionStore.open_session(session)
    older_id = SessionStore.append_investigation_result(
        session.session_id,
        {"root_cause": "older issue", "problem_md": "older report"},
        trigger="/investigate grafana",
    )
    SessionStore.append_investigation_result(
        session.session_id,
        {"root_cause": "newer issue", "problem_md": "newer report"},
        trigger="/investigate generic",
    )

    picks = iter([rca_cmds._RCA_HISTORY, older_id])
    monkeypatch.setattr(rca_cmds, "repl_tty_interactive", lambda: True)
    monkeypatch.setattr(rca_cmds, "repl_choose_one", lambda **_: next(picks))

    console, buf = _capture()
    assert dispatch_slash("/rca", session, console) is True
    output = buf.getvalue()
    assert "older issue" in output
    assert "older report" in output
    assert "newer issue" not in output


def test_rca_save_writes_markdown(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("config.constants.OPENSRE_HOME_DIR", tmp_path)
    session = ReplSession()
    SessionStore.open_session(session)
    SessionStore.append_investigation_result(
        session.session_id,
        {"root_cause": "redis timeout", "problem_md": "cache unavailable"},
        trigger="/investigate generic",
    )

    dest = tmp_path / "report.md"
    console, buf = _capture()
    assert dispatch_slash(f"/rca save {dest}", ReplSession(), console) is True
    assert (
        dest.read_text(encoding="utf-8")
        == "## Root Cause\n\nredis timeout\n\n## Report\n\ncache unavailable\n"
    )
    assert "saved:" in buf.getvalue()


def test_rca_save_by_id_writes_json(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("config.constants.OPENSRE_HOME_DIR", tmp_path)
    session = ReplSession()
    SessionStore.open_session(session)
    inv_id = SessionStore.append_investigation_result(
        session.session_id,
        {"root_cause": "bad deploy", "problem_md": "rollback required"},
        trigger="/investigate grafana",
    )

    dest = tmp_path / "report.json"
    console, buf = _capture()
    assert dispatch_slash(f"/rca save {inv_id[:4]} {dest}", ReplSession(), console) is True
    payload = json.loads(dest.read_text(encoding="utf-8"))
    assert payload["root_cause"] == "bad deploy"
    assert payload["problem_md"] == "rollback required"
    assert payload["investigation_id"] == inv_id
    assert "saved:" in buf.getvalue()


def test_rca_save_strips_quoted_path(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("config.constants.OPENSRE_HOME_DIR", tmp_path)
    session = ReplSession()
    SessionStore.open_session(session)
    inv_id = SessionStore.append_investigation_result(
        session.session_id,
        {"root_cause": "quoted path", "problem_md": "saved body"},
        trigger="/investigate generic",
    )

    dest = tmp_path / "quoted.md"
    console, buf = _capture()
    assert dispatch_slash(f"/rca save {inv_id[:4]} '{dest}'", ReplSession(), console) is True
    assert dest.read_text(encoding="utf-8").startswith("## Root Cause")
    assert "saved:" in buf.getvalue()


def test_rca_save_to_new_folder_adds_default_filename(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("config.constants.OPENSRE_HOME_DIR", tmp_path)
    session = ReplSession()
    SessionStore.open_session(session)
    inv_id = SessionStore.append_investigation_result(
        session.session_id,
        {"root_cause": "folder save", "problem_md": "in subfolder"},
        trigger="/investigate generic",
    )

    folder = tmp_path / "rca_reports"
    folder.mkdir()
    console, buf = _capture()
    assert dispatch_slash(f"/rca save {inv_id[:4]} {folder}/", ReplSession(), console) is True
    saved = folder / f"rca-{inv_id[:8]}.md"
    assert saved.exists()
    assert "folder save" in saved.read_text(encoding="utf-8")
    assert "saved:" in buf.getvalue()


def test_rca_save_to_new_folder_trailing_slash_creates_subdirectory(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("config.constants.OPENSRE_HOME_DIR", tmp_path)
    session = ReplSession()
    SessionStore.open_session(session)
    inv_id = SessionStore.append_investigation_result(
        session.session_id,
        {"root_cause": "nested save", "problem_md": "inside new folder"},
        trigger="/investigate generic",
    )

    folder = tmp_path / "new_rca_folder"
    console, buf = _capture()
    assert dispatch_slash(f"/rca save {inv_id[:4]} {folder}/", ReplSession(), console) is True
    saved = folder / f"rca-{inv_id[:8]}.md"
    assert saved.exists()
    assert "nested save" in saved.read_text(encoding="utf-8")
    assert "saved:" in buf.getvalue()


def test_rca_save_unknown_id_reports_not_found(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("config.constants.OPENSRE_HOME_DIR", tmp_path)
    session = ReplSession()
    SessionStore.open_session(session)
    SessionStore.append_investigation_result(
        session.session_id,
        {"root_cause": "existing issue", "problem_md": "existing report"},
        trigger="/investigate generic",
    )

    dest = tmp_path / "report.md"
    console, buf = _capture()
    assert dispatch_slash(f"/rca save badid {dest}", ReplSession(), console) is True
    output = buf.getvalue()
    assert "RCA report not found" in output
    assert "no persisted RCA reports yet" not in output
    assert not dest.exists()


def test_normalize_rca_save_path_strips_quotes() -> None:
    from interactive_shell.command_registry import rca_cmds

    dest = rca_cmds._normalize_rca_save_path("'/tmp/report.md'", investigation_id="abcd1234")
    assert dest == Path("/tmp/report.md")


def test_tty_rca_save_menu_picks_latest_and_prompts_path(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from interactive_shell.command_registry import rca_cmds

    monkeypatch.setattr("config.constants.OPENSRE_HOME_DIR", tmp_path)
    session = ReplSession()
    SessionStore.open_session(session)
    SessionStore.append_investigation_result(
        session.session_id,
        {"root_cause": "latest root cause", "problem_md": "latest body"},
        trigger="/investigate generic",
    )

    dest = tmp_path / "picked.md"
    monkeypatch.setattr(rca_cmds, "repl_tty_interactive", lambda: True)
    monkeypatch.setattr(rca_cmds, "repl_choose_one", lambda **_: rca_cmds._RCA_LATEST)
    monkeypatch.setattr(rca_cmds, "_prompt_rca_save_path", lambda _console: str(dest))

    console, buf = _capture()
    assert dispatch_slash("/rca save", session, console) is True
    assert "latest root cause" in dest.read_text(encoding="utf-8")
    assert "saved:" in buf.getvalue()
