"""Tests for the interactive shell loop helpers."""

from __future__ import annotations

import io
from pathlib import Path

import pytest
from prompt_toolkit.application import create_app_session
from prompt_toolkit.buffer import Buffer
from prompt_toolkit.completion import CompleteEvent
from prompt_toolkit.document import Document
from prompt_toolkit.history import FileHistory, InMemoryHistory
from prompt_toolkit.input import DummyInput
from prompt_toolkit.keys import Keys
from prompt_toolkit.output import DummyOutput

from app.cli.interactive_shell import loop


def test_repl_input_lexer_highlights_first_slash_token() -> None:
    lexer = loop.ReplInputLexer()
    get_line = lexer.lex_document(Document("/model show", len("/model")))
    fragments = get_line(0)
    cmd_frags = [(s, t) for s, t in fragments if s == "class:repl-slash-command"]
    assert cmd_frags == [("class:repl-slash-command", "/model")]
    rest = "".join(t for s, t in fragments if s == "")
    assert " show" in rest or rest.endswith(" show")


def test_repl_input_lexer_highlights_bare_help_alias() -> None:
    lexer = loop.ReplInputLexer()
    get_line = lexer.lex_document(Document("help", 4))
    fragments = get_line(0)
    assert ("class:repl-slash-command", "help") in fragments


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
    assert isinstance(prompt.completer, loop.ShellCompleter)
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


def test_shell_completer_previews_all_commands() -> None:
    completions = list(
        loop.ShellCompleter().get_completions(
            Document("/"),
            CompleteEvent(text_inserted=True),
        )
    )
    names = [completion.text for completion in completions]

    assert "/help" in names
    assert "/list" in names
    assert "/model" in names
    assert all(name.startswith("/") for name in names)


def test_shell_completer_filters_by_prefix() -> None:
    completions = list(
        loop.ShellCompleter().get_completions(
            Document("/li"),
            CompleteEvent(text_inserted=True),
        )
    )

    assert [completion.text for completion in completions] == ["/list"]


def test_shell_completer_suggests_subcommands_for_list() -> None:
    completions = list(
        loop.ShellCompleter().get_completions(
            Document("/list "),
            CompleteEvent(text_inserted=True),
        )
    )
    names = sorted({c.text for c in completions})
    assert names == ["integrations", "mcp", "models"]


def test_tab_applies_unique_slash_command_completion() -> None:
    buff = Buffer(completer=loop.ShellCompleter())
    buff.insert_text("/mod")
    loop._tab_expand_or_menu(buff)
    assert buff.text == "/model"


def test_tab_applies_unique_bareword_alias_completion() -> None:
    buff = Buffer(completer=loop.ShellCompleter())
    buff.insert_text("hel")
    loop._tab_expand_or_menu(buff)
    assert buff.text == "help"


def test_tab_with_open_completion_menu_applies_current_item() -> None:
    from prompt_toolkit.buffer import CompletionState
    from prompt_toolkit.completion import Completion

    buff = Buffer()
    buff.insert_text("/mo")
    orig_doc = buff.document
    c_model = Completion("/model", start_position=-3)
    c_mcp = Completion("/mcp", start_position=-3)
    # Assign directly — updating ``buff.document`` afterward clears ``complete_state``.
    buff.complete_state = CompletionState(orig_doc, [c_model, c_mcp], 0)

    loop._tab_expand_or_menu(buff)

    assert buff.complete_state is None
    assert buff.text == "/model"


def test_tab_with_menu_and_no_index_applies_first_choice() -> None:
    from prompt_toolkit.buffer import CompletionState
    from prompt_toolkit.completion import Completion

    buff = Buffer()
    buff.insert_text("/mo")
    orig_doc = buff.document
    c_model = Completion("/model", start_position=-3)
    c_mcp = Completion("/mcp", start_position=-3)
    buff.complete_state = CompletionState(orig_doc, [c_model, c_mcp], None)

    loop._tab_expand_or_menu(buff)

    assert buff.complete_state is None
    assert buff.text == "/model"


def test_completion_includes_tab_navigation() -> None:
    key_bindings = loop._build_prompt_key_bindings()
    keys = {binding.keys for binding in key_bindings.bindings}

    assert (Keys.Down,) in keys
    assert (Keys.Up,) in keys
    assert (Keys.Tab,) in keys
    assert (Keys.BackTab,) in keys


def test_completion_menu_current_item_uses_highlight_style() -> None:
    style = loop._build_prompt_style()
    attrs = style.get_attrs_for_style_str("class:repl-slash-command")

    assert attrs.color == "ffbe68"
    assert attrs.bgcolor == "2c1e14"
    assert attrs.bold is True

    attrs_menu = style.get_attrs_for_style_str("class:completion-menu.completion.current")

    assert attrs_menu.color == "ff7a45"
    assert attrs_menu.bgcolor == "2c1e14"
    assert attrs_menu.reverse is False
    assert attrs_menu.bold is True


def test_shell_completer_path_completion_honors_mixed_case_prefix(tmp_path: Path) -> None:
    """Regression: path fragments must not be lowercased before PathCompleter.

    On case-sensitive filesystems, a lowered prefix can stop matching real directory
    names (e.g. ``RePoRtS`` no longer matches prefix ``re``).
    """
    mixed_dir = tmp_path / "RePoRtS"
    mixed_dir.mkdir()
    (mixed_dir / "x.txt").write_text("x", encoding="utf-8")
    partial = str(tmp_path / "Re")
    line = f"/investigate {partial}"
    completions = list(
        loop.ShellCompleter().get_completions(
            Document(line, len(line)),
            CompleteEvent(text_inserted=True),
        )
    )
    assert completions
    joined = " ".join(str(c.display) for c in completions)
    assert "RePoRtS" in joined


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
