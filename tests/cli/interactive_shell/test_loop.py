"""Tests for the interactive shell loop helpers."""

from __future__ import annotations

import io
from pathlib import Path

import pytest
from prompt_toolkit.application import create_app_session
from prompt_toolkit.completion import CompleteEvent
from prompt_toolkit.document import Document
from prompt_toolkit.history import FileHistory, InMemoryHistory
from prompt_toolkit.input import DummyInput
from prompt_toolkit.keys import Keys
from prompt_toolkit.output import DummyOutput

from app.cli.interactive_shell import loop


def test_build_prompt_session_uses_persistent_history(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import app.constants as const_module

    monkeypatch.setattr(const_module, "OPENSRE_HOME_DIR", tmp_path)

    with create_app_session(input=DummyInput(), output=DummyOutput()):
        prompt = loop._build_prompt_session()

    assert isinstance(prompt.history, FileHistory)
    assert prompt.history.filename == str(tmp_path / "interactive_history")
    assert tmp_path.exists()
    assert isinstance(prompt.completer, loop.SlashCommandCompleter)
    assert prompt.app.key_bindings is not None


def test_build_prompt_session_falls_back_to_memory_history(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import app.constants as const_module

    blocked_home = tmp_path / "not-a-directory"
    blocked_home.write_text("", encoding="utf-8")
    monkeypatch.setattr(const_module, "OPENSRE_HOME_DIR", blocked_home)

    with create_app_session(input=DummyInput(), output=DummyOutput()):
        prompt = loop._build_prompt_session()

    assert isinstance(prompt.history, InMemoryHistory)


def test_slash_completer_previews_all_commands() -> None:
    completions = list(
        loop._build_slash_completer().get_completions(
            Document("/"),
            CompleteEvent(text_inserted=True),
        )
    )
    names = [completion.text for completion in completions]

    assert "/help" in names
    assert "/list" in names
    assert "/model" in names
    assert all(name.startswith("/") for name in names)


def test_slash_completer_filters_by_prefix() -> None:
    completions = list(
        loop._build_slash_completer().get_completions(
            Document("/li"),
            CompleteEvent(text_inserted=True),
        )
    )

    assert [completion.text for completion in completions] == ["/list"]


def test_slash_completer_ignores_subcommand_text() -> None:
    completions = list(
        loop._build_slash_completer().get_completions(
            Document("/list "),
            CompleteEvent(text_inserted=True),
        )
    )

    assert completions == []


def test_completion_menu_supports_up_down_navigation() -> None:
    key_bindings = loop._build_prompt_key_bindings()
    keys = {binding.keys for binding in key_bindings.bindings}

    assert (Keys.Down,) in keys
    assert (Keys.Up,) in keys


def test_completion_menu_current_item_uses_subtle_highlight() -> None:
    style = loop._build_prompt_style()
    attrs = style.get_attrs_for_style_str("class:completion-menu.completion.current")

    assert attrs.color == "ff7a45"
    assert attrs.bgcolor == "241913"
    assert attrs.reverse is False
    assert attrs.bold is False


def test_run_new_alert_marks_task_failed_on_opensre_error(monkeypatch: pytest.MonkeyPatch) -> None:
    from rich.console import Console

    from app.cli.interactive_shell.session import ReplSession
    from app.cli.interactive_shell.tasks import TaskKind, TaskStatus
    from app.cli.support.errors import OpenSREError

    def _raise(
        alert_text: str,
        context_overrides: object = None,
        cancel_requested: object = None,
    ) -> dict[str, object]:
        raise OpenSREError("integration misconfigured", suggestion="run /doctor")

    monkeypatch.setattr("app.cli.investigation.run_investigation_for_session", _raise)
    session = ReplSession()
    console = Console(file=io.StringIO(), force_terminal=False, highlight=False)
    loop._run_new_alert("High CPU alert", session, console)
    inv_tasks = [
        t for t in session.task_registry.list_recent(10) if t.kind == TaskKind.INVESTIGATION
    ]
    assert len(inv_tasks) == 1
    assert inv_tasks[0].status == TaskStatus.FAILED
    assert inv_tasks[0].error == "integration misconfigured"
