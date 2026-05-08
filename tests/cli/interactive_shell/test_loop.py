"""Tests for the interactive shell loop helpers."""

from __future__ import annotations

import asyncio
import io
import re
from pathlib import Path

import pytest
from prompt_toolkit.application import create_app_session
from prompt_toolkit.buffer import Buffer
from prompt_toolkit.completion import CompleteEvent
from prompt_toolkit.document import Document
from prompt_toolkit.history import FileHistory, InMemoryHistory
from prompt_toolkit.input import DummyInput
from prompt_toolkit.input.defaults import create_pipe_input
from prompt_toolkit.keys import Keys
from prompt_toolkit.output import DummyOutput

from app.cli.interactive_shell import loop
from app.cli.interactive_shell.prompt_surface import (
    _SHIFT_ENTER_SEQUENCE,
    ReplInputLexer,
    ShellCompleter,
    _build_prompt_key_bindings,
    _build_prompt_style,
    _tab_expand_or_menu,
)
from app.cli.interactive_shell.session import ReplSession
from app.cli.interactive_shell.theme import ANSI_RESET, PROMPT_ACCENT_ANSI


def test_repl_input_lexer_highlights_first_slash_token() -> None:
    lexer = ReplInputLexer()
    get_line = lexer.lex_document(Document("/model show", len("/model")))
    fragments = get_line(0)
    cmd_frags = [(s, t) for s, t in fragments if s == "class:repl-slash-command"]
    assert cmd_frags == [("class:repl-slash-command", "/model")]
    rest = "".join(t for s, t in fragments if s == "")
    assert " show" in rest or rest.endswith(" show")


def test_repl_input_lexer_highlights_bare_help_alias() -> None:
    lexer = ReplInputLexer()
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
    assert isinstance(prompt.completer, ShellCompleter)
    assert prompt.multiline is True
    assert prompt.reserve_space_for_menu == 0
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


def test_repl_session_prompt_history_backend_matches_prompt_toolkit_history(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import app.constants as const_module

    monkeypatch.setattr(const_module, "OPENSRE_HOME_DIR", tmp_path)
    with create_app_session(input=DummyInput(), output=DummyOutput()):
        session = ReplSession()
        prompt = loop._build_prompt_session()
        session.prompt_history_backend = prompt.history
    assert session.prompt_history_backend is prompt.history


def test_prompt_message_uses_accent_glyph() -> None:
    rendered = loop._prompt_message(ReplSession()).value

    assert PROMPT_ACCENT_ANSI in rendered
    assert "❯" in rendered
    assert ANSI_RESET in rendered


def test_shift_enter_inserts_newline_before_submit(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import app.constants as const_module

    monkeypatch.setattr(const_module, "OPENSRE_HOME_DIR", tmp_path)

    async def _collect() -> str:
        with (
            create_pipe_input() as pipe_input,
            create_app_session(input=pipe_input, output=DummyOutput()),
        ):
            prompt = loop._build_prompt_session()
            task = asyncio.create_task(prompt.prompt_async(""))
            pipe_input.send_bytes(b"first line")
            pipe_input.send_bytes(_SHIFT_ENTER_SEQUENCE.encode())
            pipe_input.send_bytes(b"second line\r")
            return await asyncio.wait_for(task, timeout=1)

    assert asyncio.run(_collect()) == "first line\nsecond line"


def test_shell_completer_previews_all_commands() -> None:
    completions = list(
        ShellCompleter().get_completions(
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
        ShellCompleter().get_completions(
            Document("/li"),
            CompleteEvent(text_inserted=True),
        )
    )

    assert [completion.text for completion in completions] == ["/list"]


def test_shell_completer_suggests_subcommands_for_list() -> None:
    completions = list(
        ShellCompleter().get_completions(
            Document("/list "),
            CompleteEvent(text_inserted=True),
        )
    )
    names = sorted({c.text for c in completions})
    assert names == ["integrations", "mcp", "models", "tools"]


def test_tab_applies_unique_slash_command_completion() -> None:
    buff = Buffer(completer=ShellCompleter())
    buff.insert_text("/mod")
    _tab_expand_or_menu(buff)
    assert buff.text == "/model"


def test_tab_applies_unique_bareword_alias_completion() -> None:
    buff = Buffer(completer=ShellCompleter())
    buff.insert_text("hel")
    _tab_expand_or_menu(buff)
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

    _tab_expand_or_menu(buff)

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

    _tab_expand_or_menu(buff)

    assert buff.complete_state is None
    assert buff.text == "/model"


def test_completion_includes_tab_navigation() -> None:
    key_bindings = _build_prompt_key_bindings()
    keys = {binding.keys for binding in key_bindings.bindings}

    assert (Keys.ControlM,) in keys
    assert (Keys.Down,) in keys
    assert (Keys.Up,) in keys
    assert (Keys.Tab,) in keys
    assert (Keys.BackTab,) in keys


def test_completion_menu_current_item_uses_highlight_style() -> None:
    style = _build_prompt_style()
    attrs = style.get_attrs_for_style_str("class:repl-slash-command")

    assert attrs.color == "5EF0E8"  # ACCENT_SOFT
    assert attrs.bgcolor == "2c1e14"
    assert attrs.bold is True

    attrs_menu = style.get_attrs_for_style_str("class:completion-menu.completion.current")

    assert attrs_menu.color == "1AFF8C"  # PRIMARY
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
        ShellCompleter().get_completions(
            Document(line, len(line)),
            CompleteEvent(text_inserted=True),
        )
    )
    assert completions
    joined = " ".join(str(c.display) for c in completions)
    assert "RePoRtS" in joined


def test_run_new_alert_marks_task_failed_on_opensre_error(monkeypatch: pytest.MonkeyPatch) -> None:
    from rich.console import Console

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


def test_run_new_alert_reports_unexpected_error(monkeypatch: pytest.MonkeyPatch) -> None:
    from rich.console import Console

    from app.cli.interactive_shell.tasks import TaskStatus

    captured_errors: list[BaseException] = []

    def _raise(
        alert_text: str,
        context_overrides: object = None,
        cancel_requested: object = None,
    ) -> dict[str, object]:
        raise RuntimeError("pipeline exploded")

    monkeypatch.setattr("app.cli.investigation.run_investigation_for_session", _raise)
    monkeypatch.setattr(
        "app.cli.support.exception_reporting.capture_exception",
        lambda exc, **_kwargs: captured_errors.append(exc),
    )
    session = ReplSession()
    console = Console(file=io.StringIO(), force_terminal=False, highlight=False)

    loop._run_new_alert("High CPU alert", session, console)

    inv_tasks = session.task_registry.list_recent(10)
    assert inv_tasks[0].status == TaskStatus.FAILED
    assert len(captured_errors) == 1
    assert isinstance(captured_errors[0], RuntimeError)


def test_run_new_alert_does_not_report_opensre_error(monkeypatch: pytest.MonkeyPatch) -> None:
    from rich.console import Console

    from app.cli.support.errors import OpenSREError

    captured_errors: list[BaseException] = []

    def _raise(
        alert_text: str,
        context_overrides: object = None,
        cancel_requested: object = None,
    ) -> dict[str, object]:
        raise OpenSREError("integration misconfigured")

    monkeypatch.setattr("app.cli.investigation.run_investigation_for_session", _raise)
    monkeypatch.setattr(
        "app.cli.support.exception_reporting.capture_exception",
        lambda exc, **_kwargs: captured_errors.append(exc),
    )
    session = ReplSession()
    console = Console(file=io.StringIO(), force_terminal=False, highlight=False)

    loop._run_new_alert("High CPU alert", session, console)

    assert captured_errors == []


def test_run_one_turn_reports_slash_dispatch_error(monkeypatch: pytest.MonkeyPatch) -> None:
    from rich.console import Console

    class _Prompt:
        async def prompt_async(self, _prompt: object) -> str:
            return "/boom"

    captured_errors: list[BaseException] = []

    def _boom(*_args: object, **_kwargs: object) -> bool:
        raise RuntimeError("handler crashed")

    monkeypatch.setattr(loop, "dispatch_slash", _boom)
    monkeypatch.setattr(
        "app.cli.support.exception_reporting.capture_exception",
        lambda exc, **_kwargs: captured_errors.append(exc),
    )
    session = ReplSession()
    console = Console(file=io.StringIO(), force_terminal=False, highlight=False)

    should_continue = asyncio.run(loop._run_one_turn(_Prompt(), session, console))

    assert should_continue is True
    assert len(captured_errors) == 1
    assert isinstance(captured_errors[0], RuntimeError)


def test_run_one_turn_renders_submitted_prompt_before_handler(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from rich.console import Console

    class _Prompt:
        async def prompt_async(self, _prompt: object) -> str:
            return "explain deploy"

    monkeypatch.setattr(loop, "classify_input", lambda *_args: "cli_help")
    monkeypatch.setattr(loop, "answer_cli_help", lambda *_args, **_kwargs: None)

    buf = io.StringIO()
    console = Console(file=buf, force_terminal=True, color_system=None, highlight=False)

    should_continue = asyncio.run(loop._run_one_turn(_Prompt(), ReplSession(), console))

    output = re.sub(r"\x1b\[[0-9;?]*[A-Za-z]", "", buf.getvalue())
    assert should_continue is True
    assert "❯" in output
    assert "explain deploy" in output
