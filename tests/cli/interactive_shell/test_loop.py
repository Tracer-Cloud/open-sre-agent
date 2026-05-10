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

from app.cli.interactive_shell import loop, prompt_surface
from app.cli.interactive_shell.prompt_surface import (
    _SHIFT_ENTER_SEQUENCE,
    ReplInputLexer,
    ShellCompleter,
    _build_prompt_key_bindings,
    _build_prompt_style,
    _tab_expand_or_menu,
)
from app.cli.interactive_shell.router import RouteDecision, RouteKind
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
        prompt = prompt_surface._build_prompt_session()

    assert isinstance(prompt.history, FileHistory)
    assert prompt.history.filename == str(tmp_path / "interactive_history")
    assert tmp_path.exists()
    assert isinstance(prompt.completer, ShellCompleter)
    assert prompt.multiline is True
    assert prompt.reserve_space_for_menu == 8
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
        prompt = prompt_surface._build_prompt_session()

    assert isinstance(prompt.history, InMemoryHistory)


def test_repl_session_prompt_history_backend_matches_prompt_toolkit_history(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import app.constants as const_module

    monkeypatch.setattr(const_module, "OPENSRE_HOME_DIR", tmp_path)
    with create_app_session(input=DummyInput(), output=DummyOutput()):
        session = ReplSession()
        prompt = prompt_surface._build_prompt_session()
        session.prompt_history_backend = prompt.history
    assert session.prompt_history_backend is prompt.history


def test_prompt_message_uses_accent_glyph() -> None:
    rendered = prompt_surface._prompt_message(ReplSession()).value

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
            prompt = prompt_surface._build_prompt_session()
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
    assert "/effort" in names
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


def test_shell_completer_suggests_effort_levels() -> None:
    completions = list(
        ShellCompleter().get_completions(
            Document("/effort "),
            CompleteEvent(text_inserted=True),
        )
    )
    names = sorted({c.text for c in completions})
    assert names == ["high", "low", "max", "medium", "xhigh"]


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
    from app.cli.interactive_shell.theme import BG, HIGHLIGHT

    style = _build_prompt_style()
    attrs = style.get_attrs_for_style_str("class:repl-slash-command")

    assert attrs.color == HIGHLIGHT.lstrip("#")
    assert attrs.bgcolor == BG.lstrip("#")
    assert attrs.bold is True

    attrs_menu = style.get_attrs_for_style_str("class:completion-menu.completion.current")

    assert attrs_menu.color == HIGHLIGHT.lstrip("#")
    assert attrs_menu.bgcolor == BG.lstrip("#")
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


def test_dispatch_one_turn_reports_slash_dispatch_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from rich.console import Console

    captured_errors: list[BaseException] = []
    exit_calls: list[None] = []

    def _boom(*_args: object, **_kwargs: object) -> bool:
        raise RuntimeError("handler crashed")

    monkeypatch.setattr(loop, "dispatch_slash", _boom)
    monkeypatch.setattr(
        "app.cli.support.exception_reporting.capture_exception",
        lambda exc, **_kwargs: captured_errors.append(exc),
    )
    session = ReplSession()
    console = Console(file=io.StringIO(), force_terminal=False, highlight=False)

    loop._dispatch_one_turn("/boom", session, console, on_exit=lambda: exit_calls.append(None))

    # The error path catches the exception, prints a "command error" line,
    # and continues — must NOT request exit, since the REPL stays alive.
    assert exit_calls == []
    assert len(captured_errors) == 1
    assert isinstance(captured_errors[0], RuntimeError)


def test_dispatch_one_turn_typoed_bare_alias_dispatches_canonical_slash(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Bare-alias typos (e.g. ``hlep`` → ``/help``) are normalised by
    ``_router.slash_dispatch_text`` before reaching ``dispatch_slash``.

    Adapted from main's ``test_run_one_turn_typoed_bare_alias_...``: that
    test exercised main's ``_run_one_turn`` async wrapper, which doesn't
    exist in this branch's queue + processor architecture. The behaviour
    being verified — that bare aliases route to canonical slash form —
    lives inside ``_dispatch_one_turn`` (the slash-kind branch calls
    ``_router.slash_dispatch_text``), so the test now drives that
    function directly.
    """
    from rich.console import Console

    dispatched: list[str] = []

    def _dispatch(command: str, *_args: object, **_kwargs: object) -> bool:
        dispatched.append(command)
        return True

    monkeypatch.setattr(
        loop,
        "route_input",
        lambda *_args: RouteDecision(RouteKind.SLASH, 0.98, ("bare_command_alias",)),
    )
    monkeypatch.setattr(loop, "dispatch_slash", _dispatch)
    session = ReplSession()
    console = Console(file=io.StringIO(), force_terminal=False, highlight=False)

    loop._dispatch_one_turn("hlep", session, console, on_exit=lambda: None)

    assert dispatched == ["/help"]


def test_dispatch_one_turn_routes_to_cli_help_for_help_questions(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Verify the routing decision drives the right handler.

    Replaces the old ``test_run_one_turn_renders_submitted_prompt_before_handler``
    which asserted on PromptSession echo behaviour — that responsibility now
    lives in :func:`_run_interactive` (the prompt-toolkit loop, which calls
    :func:`render_submitted_prompt` after each ``prompt_async`` return).
    """
    from rich.console import Console

    answered_with: list[str] = []

    monkeypatch.setattr(
        loop,
        "route_input",
        lambda *_args: RouteDecision(RouteKind.CLI_HELP, 0.9, ("test",)),
    )
    monkeypatch.setattr(
        loop,
        "answer_cli_help",
        lambda text, _session, _console: answered_with.append(text),
    )

    session = ReplSession()
    console = Console(file=io.StringIO(), force_terminal=False, highlight=False)
    loop._dispatch_one_turn("explain deploy", session, console, on_exit=lambda: None)

    assert answered_with == ["explain deploy"]


def test_dispatch_one_turn_calls_on_exit_when_slash_returns_false(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Slash commands like /exit return False from dispatch_slash.

    The persistent REPL relies on ``on_exit`` to translate that signal into
    ``app.exit()`` — without this, /exit would silently no-op.
    """
    from rich.console import Console

    monkeypatch.setattr(loop, "dispatch_slash", lambda *_args, **_kwargs: False)

    session = ReplSession()
    console = Console(file=io.StringIO(), force_terminal=False, highlight=False)
    exit_calls: list[None] = []

    loop._dispatch_one_turn("/exit", session, console, on_exit=lambda: exit_calls.append(None))

    assert exit_calls == [None]


class TestLooksLikeCorrection:
    """Unit tests for the ``_looks_like_correction`` heuristic.

    Pins the v1 catches, false-positive guards, and limitations so future
    iteration on ``_INTERVENTION_CORRECTION_RE`` has a regression baseline.
    """

    @pytest.mark.parametrize(
        "text",
        [
            "no, do that instead",
            "nope",
            "nvm",
            "never mind",
            "actually, let's check Datadog first",
            "scratch that, run the synthetic test",
            "wait, wrong dashboard",
            "let's do an EKS health check instead",
            "try a token refresh instead",
            "wrong dashboard, fix it",
            "instead, log a warning",
            "Wait!",  # case-insensitive
            "NO.",
        ],
    )
    def test_correction_cues_match(self, text: str) -> None:
        assert loop._looks_like_correction(text) is True

    @pytest.mark.parametrize(
        "text",
        [
            # Punctuation-lookahead guards reject content uses of cue words.
            "stop the server before redeploying",
            "instead of returning null, log a warning",
            "no problem, I'll handle it",
            "wait for the result",
            # v1 limitation: cue must be at start of message.
            "hmm, scratch that",
            "doesn't work, try X instead",
            # Edge cases.
            "",
            "   ",
            "```\nstop the server\n```",
        ],
    )
    def test_non_correction_text_does_not_match(self, text: str) -> None:
        assert loop._looks_like_correction(text) is False


# ── Spinner state tests ──────────────────────────────────────────────────────


def _strip_ansi(text: str) -> str:
    return re.sub(r"\x1b\[[0-9;?]*[A-Za-z]", "", text)


class TestSpinnerState:
    """``_SpinnerState`` holds the live-stream indicator state and renders
    two ANSI views: the inline spinner above the input frame
    (``inline_spinner_ansi``) and the bottom toolbar hint
    (``toolbar_ansi``).
    """

    def test_idle_state_emits_no_inline_spinner(self) -> None:
        spinner = loop._SpinnerState()
        assert spinner.streaming is False
        assert spinner.inline_spinner_ansi() == ""

    def test_streaming_inline_spinner_includes_glyph_and_token_count(self) -> None:
        spinner = loop._SpinnerState()
        spinner.start()
        spinner.bytes_in = 1234 * loop._SpinnerState._CHARS_PER_TOKEN  # = 1234 tokens
        rendered = _strip_ansi(spinner.inline_spinner_ansi())
        # The verb is randomly picked from ``_THINKING_VERBS`` per turn —
        # any of them followed by ``…`` is acceptable.
        assert any(f"{verb}…" in rendered for verb in spinner._THINKING_VERBS)
        # 1234 tokens → "1.2k" via format_token_count_short.
        assert "1.2k tokens" in rendered
        # Spinner glyph from the brail palette.
        assert any(g in rendered for g in spinner._SPINNER_FRAMES)

    def test_streaming_inline_spinner_verb_stays_constant_across_calls(self) -> None:
        """A turn's verb is fixed at ``start()`` so the indicator
        doesn't flicker between words mid-stream."""
        spinner = loop._SpinnerState()
        spinner.start()
        verbs_seen: set[str] = set()
        for _ in range(20):
            rendered = _strip_ansi(spinner.inline_spinner_ansi())
            for verb in spinner._THINKING_VERBS:
                if f"{verb}…" in rendered:
                    verbs_seen.add(verb)
                    break
        assert len(verbs_seen) == 1, f"verb changed mid-turn — saw {verbs_seen}"

    def test_inline_spinner_glyph_animates_across_calls(self) -> None:
        """Each render advances the frame index — animation in place."""
        spinner = loop._SpinnerState()
        spinner.start()
        seen = {
            _extract_glyph(spinner.inline_spinner_ansi(), spinner._SPINNER_FRAMES)
            for _ in range(len(spinner._SPINNER_FRAMES) * 2)
        }
        # Over two full rotations we should see every frame.
        assert seen == set(spinner._SPINNER_FRAMES)

    def test_stop_returns_to_idle_state(self) -> None:
        spinner = loop._SpinnerState()
        spinner.start()
        assert spinner.streaming is True
        spinner.stop()
        assert spinner.streaming is False
        assert spinner.inline_spinner_ansi() == ""

    def test_toolbar_idle_hint_lists_shortcut_keys(self) -> None:
        """When idle, toolbar should advertise the keys the user can press."""
        spinner = loop._SpinnerState()
        rendered = _strip_ansi(spinner.toolbar_ansi().value)
        assert "esc to clear" in rendered
        assert "/ for commands" in rendered
        assert "history" in rendered

    def test_toolbar_streaming_hint_says_interrupt(self) -> None:
        """During streaming the toolbar hint switches to ``esc to interrupt``."""
        spinner = loop._SpinnerState()
        spinner.start()
        rendered = _strip_ansi(spinner.toolbar_ansi().value)
        assert "esc to interrupt" in rendered
        # Idle hint should NOT be shown when streaming.
        assert "esc to clear" not in rendered


def _extract_glyph(ansi_text: str, frames: tuple[str, ...]) -> str:
    plain = _strip_ansi(ansi_text)
    for g in frames:
        if g in plain:
            return g
    return ""


# ── Streaming-console adapter tests ──────────────────────────────────────────


class TestStreamingConsole:
    """``_StreamingConsole`` is the bridge between the streaming layer and
    the prompt-toolkit spinner. It is the only way the dispatch worker
    thread can signal back to the prompt: progress updates and
    cancellation polling go through this object's optional methods.
    """

    def test_update_progress_writes_to_spinner_state(self) -> None:
        import threading as _threading

        spinner = loop._SpinnerState()
        spinner.start()
        cancel = _threading.Event()
        console = loop._StreamingConsole(
            spinner,
            cancel,
            highlight=False,
            force_terminal=True,
            color_system=None,
        )
        console.update_streaming_progress(4096)
        assert spinner.bytes_in == 4096

    def test_cancel_requested_reflects_event_state(self) -> None:
        import threading as _threading

        spinner = loop._SpinnerState()
        cancel = _threading.Event()
        console = loop._StreamingConsole(
            spinner,
            cancel,
            highlight=False,
            force_terminal=True,
            color_system=None,
        )
        assert console.cancel_requested is False
        cancel.set()
        assert console.cancel_requested is True
        cancel.clear()
        assert console.cancel_requested is False


# ── ReplState dataclass tests ────────────────────────────────────────────────


class TestReplState:
    """``_ReplState`` is the single owner of the cancellation primitives
    shared between the prompt loop, the queue processor, and the
    Esc/Ctrl+L key bindings. Its methods exist so callers don't poke
    raw fields and re-derive ``is_running`` everywhere.
    """

    def test_default_state_is_idle(self) -> None:
        state = loop._ReplState()
        assert state.is_dispatch_running() is False
        assert state.exit_requested is False
        # No active dispatch → no cancel event parked.
        assert state.current_cancel_event is None
        assert state.queue.empty()

    def test_is_dispatch_running_tracks_task_lifecycle(self) -> None:
        async def _scenario() -> None:
            state = loop._ReplState()

            async def _slow() -> None:
                await asyncio.sleep(0.05)

            state.current_task = asyncio.create_task(_slow())
            assert state.is_dispatch_running() is True
            await state.current_task
            assert state.is_dispatch_running() is False

        asyncio.run(_scenario())

    def test_cancel_current_dispatch_signals_event_and_task(self) -> None:
        async def _scenario() -> None:
            import threading as _threading

            state = loop._ReplState()
            dispatch_cancel = _threading.Event()
            state.current_cancel_event = dispatch_cancel

            async def _waits_forever() -> None:
                # Long sleep — only the cancel can interrupt this.
                await asyncio.sleep(1.0)

            state.current_task = asyncio.create_task(_waits_forever())
            # Snapshot to a local — re-reading ``state.current_task``
            # across the cancel + await would let CodeQL / code-quality
            # bots flag the bare ``await`` as ineffectual and would also
            # leave the assertion racey if anything reassigned the field.
            task = state.current_task
            state.cancel_current_dispatch()

            # Both signals must fire — per-dispatch event flipped AND
            # the asyncio task cancelled.
            assert dispatch_cancel.is_set() is True
            try:  # noqa: SIM105
                await task
            except asyncio.CancelledError:
                # Expected — that's the whole point of the cancel.
                pass
            assert task.cancelled() is True

        asyncio.run(_scenario())

    def test_cancel_when_no_task_is_a_no_op(self) -> None:
        """``cancel_current_dispatch`` is idempotent — safe to call when
        nothing is running. With no active dispatch parked,
        ``current_cancel_event`` is ``None`` and there's nothing to flip."""
        state = loop._ReplState()
        state.cancel_current_dispatch()
        assert state.is_dispatch_running() is False
        assert state.current_cancel_event is None


# ── Cancel key bindings ──────────────────────────────────────────────────────


class TestBuildCancelKeyBindings:
    """``_build_cancel_key_bindings`` returns a ``KeyBindings`` with two
    handlers — Esc and Ctrl+L. The handlers are extracted out of the
    prompt loop so they can be exercised without the full async
    machinery; this test instantiates the bindings and verifies they
    were registered for the right keys."""

    def test_returns_bindings_for_escape_and_ctrl_l(self) -> None:
        state = loop._ReplState()
        kb = loop._build_cancel_key_bindings(state)
        # Flatten each binding's keys tuple. ``Keys`` enum members have
        # ``.value`` strings like ``"escape"``/``"c-l"`` matching the
        # decorator argument; plain string keys are themselves.
        registered = {getattr(k, "value", k) for b in kb.bindings for k in b.keys}
        assert "escape" in registered, f"escape binding missing — registered: {registered}"
        assert "c-l" in registered, f"Ctrl+L binding missing — registered: {registered}"
