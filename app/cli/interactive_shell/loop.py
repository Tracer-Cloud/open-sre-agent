"""Async REPL loop — the zero-exit heart of the OpenSRE interactive terminal."""

from __future__ import annotations

import asyncio
import sys
from collections.abc import Callable, Iterable

from prompt_toolkit import PromptSession
from prompt_toolkit.buffer import Buffer
from prompt_toolkit.completion import CompleteEvent, Completer, Completion, PathCompleter
from prompt_toolkit.document import Document
from prompt_toolkit.filters import has_completions
from prompt_toolkit.formatted_text import ANSI, StyleAndTextTuples
from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.lexers import Lexer
from prompt_toolkit.styles import Style
from rich.console import Console
from rich.markup import escape
from rich.rule import Rule

from app.cli.interactive_shell.agent_actions import execute_cli_actions
from app.cli.interactive_shell.banner import render_banner
from app.cli.interactive_shell.cli_agent import answer_cli_agent
from app.cli.interactive_shell.cli_help import answer_cli_help
from app.cli.interactive_shell.commands import SLASH_COMMANDS, dispatch_slash
from app.cli.interactive_shell.config import ReplConfig
from app.cli.interactive_shell.follow_up import answer_follow_up
from app.cli.interactive_shell.history import load_prompt_history
from app.cli.interactive_shell.router import BARE_COMMAND_ALIASES, classify_input
from app.cli.interactive_shell.session import ReplSession
from app.cli.interactive_shell.theme import (
    ANSI_RESET,
    DIM_COUNTER_ANSI,
    OPENCLAW_AMBER,
    OPENCLAW_CORAL,
    OPENCLAW_ORANGE,
    PROMPT_ACCENT_ANSI,
    SEPARATOR_COLOR,
    TERMINAL_ERROR,
)
from app.cli.support.errors import OpenSREError


class ReplInputLexer(Lexer):
    """Style the command token (slash form or bare alias) like Claude Code."""

    _CMD_STYLE = "class:repl-slash-command"

    def lex_document(self, document: Document) -> Callable[[int], StyleAndTextTuples]:
        lines = document.lines

        def get_line(lineno: int) -> StyleAndTextTuples:
            try:
                line = lines[lineno]
            except IndexError:
                return []
            if not line:
                return [("", line)]
            leading = len(line) - len(line.lstrip(" \t"))
            lead, stripped = line[:leading], line[leading:]
            if not stripped:
                return [("", line)]

            if stripped.startswith("/"):
                i = 0
                while i < len(stripped) and not stripped[i].isspace():
                    i += 1
                cmd, rest = stripped[:i], stripped[i:]
                out: StyleAndTextTuples = []
                if lead:
                    out.append(("", lead))
                out.append((self._CMD_STYLE, cmd))
                if rest:
                    out.append(("", rest))
                return out

            parts = stripped.split(maxsplit=1)
            first = parts[0]
            tail = stripped[len(first) :]
            if first.lower() in BARE_COMMAND_ALIASES:
                bare_line: StyleAndTextTuples = []
                if lead:
                    bare_line.append(("", lead))
                bare_line.append((self._CMD_STYLE, first))
                if tail:
                    bare_line.append(("", tail))
                return bare_line

            return [("", line)]

        return get_line


def _prompt_line_ansi(session: ReplSession) -> ANSI:
    """Context-aware prompt: ``[n] ❯`` after the first completed turn.

    The chevron uses the accent colour; a normal space **after** ``ANSI_RESET``
    separates user input from the glyph (clearer in emoji-aware terminals).
    """
    if session.history:
        counter = len(session.history)
        prefix = f"{DIM_COUNTER_ANSI}[{counter}]{ANSI_RESET} "
    else:
        prefix = ""
    return ANSI(f"{prefix}{PROMPT_ACCENT_ANSI}❯{ANSI_RESET} ")


def _print_turn_separator(console: Console) -> None:
    """Hairline between conversational turns."""
    console.print(Rule(style=SEPARATOR_COLOR))


def _short_meta(text: str, max_len: int = 54) -> str:
    """Trim completion help text to fit the meta column."""
    return text if len(text) <= max_len else text[: max_len - 1] + "…"


class ShellCompleter(Completer):
    """Tab-completion for slash commands, subcommands, file paths, and bare-word aliases.

    Completion levels:
      1. Bare-word aliases (``help``, ``exit``, …) — no leading slash, no spaces.
      2. Top-level slash command names (``/hel`` → ``/help``).
      3. First-arg keywords from each command's registry metadata (e.g. ``/model `` → hints).
      4. File-path completion for ``/investigate`` and ``/save``.
    """

    def get_completions(
        self,
        document: Document,
        complete_event: CompleteEvent,  # noqa: ARG002 - required by prompt_toolkit protocol
    ) -> Iterable[Completion]:
        text = document.text_before_cursor
        if not text:
            return

        # ── Bare-word alias (no slash, no spaces) ───────────────────────────────
        if not text.startswith("/"):
            if " " in text:
                return
            needle = text.lower()
            for alias in sorted(BARE_COMMAND_ALIASES):
                if alias.startswith(needle) and alias != needle:
                    yield Completion(
                        alias,
                        start_position=-len(text),
                        display=alias,
                        display_meta="command shortcut",
                    )
            return

        # ── Slash-prefixed input ─────────────────────────────────────────────────
        parts = text.split()
        trailing_space = text != text.rstrip(" ")

        # Level 0: /[partial] → match top-level command names
        if len(parts) == 1 and not trailing_space:
            needle = parts[0].lower()
            for cmd in SLASH_COMMANDS.values():
                if cmd.name.lower().startswith(needle):
                    yield Completion(
                        cmd.name,
                        start_position=-len(parts[0]),
                        display=cmd.name,
                        display_meta=_short_meta(cmd.help_text),
                    )
            return

        # Level 1: /command [partial] → subcommand keywords or file paths
        if len(parts) <= 2:
            cmd_name = parts[0].lower()
            raw_arg = "" if trailing_space or len(parts) < 2 else parts[1]

            # File-path completion — pass ``raw_arg`` verbatim. ``PathCompleter``
            # matches ``os.listdir`` names with case-sensitive ``startswith`` on
            # Linux and case-sensitive macOS volumes; lowering breaks mixed-case paths.
            if cmd_name in ("/investigate", "/save"):
                path_prefix = raw_arg
                yield from PathCompleter(expanduser=True).get_completions(
                    Document(path_prefix, len(path_prefix)), complete_event
                )
                return

            entry = SLASH_COMMANDS.get(cmd_name)
            hints = entry.first_arg_completions if entry is not None else ()
            sub_prefix = raw_arg.lower()
            for sub, meta in hints:
                if sub.startswith(sub_prefix):
                    yield Completion(
                        sub,
                        start_position=-len(raw_arg),
                        display=sub,
                        display_meta=meta,
                    )


def _tab_expand_or_menu(buffer: Buffer) -> None:
    """Complete Tab behaviour for the REPL.

    - Menu already open: **accept** the highlighted entry (no extra Enter).
    - Otherwise, if exactly one match: apply it; if several: open the menu.

    Use ↑/↓ to change the highlighted row when multiple completions are shown.
    """
    if buffer.complete_state:
        state = buffer.complete_state
        completion = state.current_completion
        if completion is None and state.completions:
            completion = state.completions[0]
        if completion is not None:
            buffer.apply_completion(completion)
        return
    if buffer.completer is None:
        return
    completions = list(
        buffer.completer.get_completions(
            buffer.document,
            CompleteEvent(completion_requested=True),
        )
    )
    if len(completions) == 1:
        buffer.apply_completion(completions[0])
    else:
        buffer.start_completion(select_first=True)


def _build_prompt_session() -> PromptSession[str]:
    return PromptSession(
        completer=ShellCompleter(),
        complete_while_typing=True,
        reserve_space_for_menu=8,
        history=load_prompt_history(),
        lexer=ReplInputLexer(),
        key_bindings=_build_prompt_key_bindings(),
        style=_build_prompt_style(),
    )


def _build_prompt_key_bindings() -> KeyBindings:
    bindings = KeyBindings()

    @bindings.add("tab")
    def _tab_complete(event: object) -> None:
        _tab_expand_or_menu(event.current_buffer)  # type: ignore[attr-defined]

    @bindings.add("s-tab")
    def _shift_tab_complete(event: object) -> None:
        buff = event.current_buffer  # type: ignore[attr-defined]
        if buff.complete_state:
            buff.complete_previous()
        else:
            buff.start_completion(select_first=False)

    @bindings.add("down", filter=has_completions)
    def _next_completion(event: object) -> None:
        event.current_buffer.complete_next()  # type: ignore[attr-defined]

    @bindings.add("up", filter=has_completions)
    def _previous_completion(event: object) -> None:
        event.current_buffer.complete_previous()  # type: ignore[attr-defined]

    return bindings


def _build_prompt_style() -> Style:
    return Style.from_dict(
        {
            "repl-slash-command": f"bold {OPENCLAW_AMBER} bg:#2c1e14",
            "completion-menu": "bg:#1c1917",
            "completion-menu.completion": "#d6d0ca bg:#1c1917",
            "completion-menu.completion.current": f"bold {OPENCLAW_ORANGE} bg:#2c1e14",
            "completion-menu.meta.completion": "#6b6561 bg:#1c1917",
            "completion-menu.meta.completion.current": f"{OPENCLAW_AMBER} bg:#2c1e14",
            "completion-menu.border": OPENCLAW_CORAL,
            "scrollbar.background": "bg:#1c1917",
            "scrollbar.button": "bg:#4a3020",
        }
    )


def _run_new_alert(
    text: str,
    session: ReplSession,
    console: Console,
    *,
    confirm_fn: Callable[[str], str] | None = None,
    is_tty: bool | None = None,
) -> None:
    """Dispatch a free-text alert description to the streaming pipeline."""
    from app.cli.interactive_shell.execution_policy import (
        evaluate_investigation_launch,
        execution_allowed,
    )
    from app.cli.interactive_shell.tasks import TaskKind
    from app.cli.investigation import run_investigation_for_session

    policy = evaluate_investigation_launch(action_type="investigation")
    if not execution_allowed(
        policy,
        session=session,
        console=console,
        action_summary="run RCA investigation from pasted alert text",
        confirm_fn=confirm_fn,
        is_tty=is_tty,
    ):
        session.record("alert", text, ok=False)
        return

    task = session.task_registry.create(TaskKind.INVESTIGATION)
    task.mark_running()
    try:
        final_state = run_investigation_for_session(
            alert_text=text,
            context_overrides=session.accumulated_context or None,
            cancel_requested=task.cancel_requested,
        )
    except KeyboardInterrupt:
        task.mark_cancelled()
        console.print("[yellow]investigation cancelled.[/yellow]")
        session.record("alert", text, ok=False)
        return
    except OpenSREError as exc:
        task.mark_failed(str(exc))
        console.print(f"[{TERMINAL_ERROR}]investigation failed:[/] {escape(str(exc))}")
        if exc.suggestion:
            console.print(f"[yellow]suggestion:[/yellow] {escape(exc.suggestion)}")
        session.record("alert", text, ok=False)
        return
    except Exception as exc:  # noqa: BLE001
        task.mark_failed(str(exc))
        # Exception repr may contain brackets (stack frame refs, config
        # dicts) that Rich would eat as markup tags — escape before printing.
        console.print(f"[{TERMINAL_ERROR}]investigation failed:[/] {escape(str(exc))}")
        session.record("alert", text, ok=False)
        return

    root = final_state.get("root_cause")
    task.mark_completed(result=str(root) if root is not None else "")
    session.last_state = final_state
    session.accumulate_from_state(final_state)
    session.record("alert", text)


async def _run_one_turn(
    prompt: PromptSession[str],
    session: ReplSession,
    console: Console,
) -> bool:
    """Read one line of input and dispatch. Returns False to exit."""
    try:
        text = await prompt.prompt_async(_prompt_line_ansi(session))
    except (EOFError, KeyboardInterrupt):
        console.print()
        return False

    text = text.strip()
    if not text:
        return True

    kind = classify_input(text, session)
    if kind == "slash":
        # Rewrite bare-word commands to their slash form before dispatch.
        cmd_text = text if text.startswith("/") else f"/{text}"
        try:
            should_continue = dispatch_slash(cmd_text, session, console)
        except Exception as exc:  # noqa: BLE001
            console.print(
                f"[{TERMINAL_ERROR}]command error:[/] {escape(str(exc))}"
                " [dim](the REPL is still running)[/dim]"
            )
            should_continue = True
        console.print()
        return should_continue

    if kind == "cli_help":
        answer_cli_help(text, session, console)
        session.record("cli_help", text)
        _print_turn_separator(console)
        return True

    if kind == "cli_agent":
        if execute_cli_actions(text, session, console):
            _print_turn_separator(console)
            return True
        answer_cli_agent(text, session, console)
        session.record("cli_agent", text)
        _print_turn_separator(console)
        return True

    if kind == "new_alert":
        _run_new_alert(text, session, console)
        _print_turn_separator(console)
        return True

    # follow_up — grounded answer against session.last_state
    answer_follow_up(text, session, console)
    session.record("follow_up", text)
    _print_turn_separator(console)
    return True


async def _repl_main(initial_input: str | None = None, config: ReplConfig | None = None) -> int:  # noqa: ARG001
    # force_terminal + truecolor so Rich always emits full ANSI, even after
    # prompt_toolkit has claimed and released stdout for input handling.
    # Without this, slash-command output after the first prompt renders as
    # literal escape codes in some terminal emulators.
    console = Console(highlight=False, force_terminal=True, color_system="truecolor")
    render_banner(console)
    session = ReplSession()
    prompt = _build_prompt_session()

    # Allow a single pre-seeded input for test harnesses
    if initial_input:
        for line in initial_input.splitlines():
            stripped = line.strip()
            if not stripped:
                continue
            kind = classify_input(stripped, session)
            if kind == "slash":
                cmd_text = stripped if stripped.startswith("/") else f"/{stripped}"
                if not dispatch_slash(cmd_text, session, console):
                    return 0
                console.print()
            elif kind == "cli_help":
                answer_cli_help(stripped, session, console)
                session.record("cli_help", stripped)
                _print_turn_separator(console)
            elif kind == "cli_agent":
                if not execute_cli_actions(stripped, session, console):
                    answer_cli_agent(stripped, session, console)
                    session.record("cli_agent", stripped)
                _print_turn_separator(console)
            elif kind == "new_alert":
                _run_new_alert(stripped, session, console)
                _print_turn_separator(console)
            else:
                answer_follow_up(stripped, session, console)
                session.record("follow_up", stripped)
                _print_turn_separator(console)

    while True:
        should_continue = await _run_one_turn(prompt, session, console)
        if not should_continue:
            return 0


def run_repl(initial_input: str | None = None, config: ReplConfig | None = None) -> int:
    """Enter the interactive REPL. Returns the exit code."""
    cfg = config or ReplConfig.load()

    if not cfg.enabled:
        return 0

    if not sys.stdin.isatty() and initial_input is None:
        # In non-TTY contexts (piped input, CI), don't start an interactive loop.
        # Callers should use `opensre investigate` instead.
        return 0

    try:
        return asyncio.run(_repl_main(initial_input=initial_input, config=cfg))
    except (EOFError, KeyboardInterrupt):
        return 0


__all__ = ["run_repl"]
