"""Async REPL loop — the zero-exit heart of the OpenSRE interactive terminal."""

from __future__ import annotations

import asyncio
import sys
from collections.abc import Iterable

from prompt_toolkit import PromptSession
from prompt_toolkit.completion import CompleteEvent, Completer, Completion, PathCompleter
from prompt_toolkit.document import Document
from prompt_toolkit.filters import has_completions
from prompt_toolkit.formatted_text import ANSI
from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.styles import Style
from rich.console import Console
from rich.markup import escape

from app.cli.interactive_shell.agent_actions import execute_cli_actions
from app.cli.interactive_shell.banner import render_banner
from app.cli.interactive_shell.cli_agent import answer_cli_agent
from app.cli.interactive_shell.cli_help import answer_cli_help
from app.cli.interactive_shell.commands import SLASH_COMMANDS, dispatch_slash
from app.cli.interactive_shell.config import ReplConfig
from app.cli.interactive_shell.follow_up import answer_follow_up
from app.cli.interactive_shell.history import load_prompt_history
from app.cli.interactive_shell.router import _BARE_COMMAND_ALIASES, classify_input
from app.cli.interactive_shell.session import ReplSession
from app.cli.interactive_shell.theme import (
    ANSI_RESET,
    OPENCLAW_AMBER,
    OPENCLAW_CORAL,
    OPENCLAW_ORANGE,
    PROMPT_ACCENT_ANSI,
)
from app.cli.support.errors import OpenSREError


def _short_meta(text: str, max_len: int = 54) -> str:
    """Trim completion help text to fit the meta column."""
    return text if len(text) <= max_len else text[: max_len - 1] + "…"


class ShellCompleter(Completer):
    """Tab-completion for slash commands, subcommands, file paths, and bare-word aliases.

    Completion levels:
      1. Bare-word aliases (``help``, ``exit``, …) — no leading slash, no spaces.
      2. Top-level slash command names (``/hel`` → ``/help``).
      3. Subcommand keywords  (``/model `` → ``show / set / toolcall``).
      4. File-path completion for ``/investigate`` and ``/save``.
    """

    _SUBCOMMANDS: dict[str, list[tuple[str, str]]] = {
        "/model": [
            ("show", "show active provider and models"),
            ("set", "switch provider  ·  /model set <provider> [model]"),
            ("toolcall", "manage toolcall model for the active provider"),
        ],
        "/integrations": [
            ("list", "list all configured integrations"),
            ("verify", "run health checks on all integrations"),
            ("show", "show details for a single integration"),
        ],
        "/list": [
            ("integrations", "alert-source integrations"),
            ("models", "active LLM models"),
            ("mcp", "connected MCP servers"),
        ],
        "/mcp": [
            ("list", "list connected MCP servers"),
            ("connect", "add an MCP server via opensre integrations setup"),
            ("disconnect", "remove an MCP server"),
        ],
        "/template": [
            ("generic", "generic alert JSON template"),
            ("datadog", "Datadog monitor alert template"),
            ("grafana", "Grafana alert template"),
            ("honeycomb", "Honeycomb trigger template"),
            ("coralogix", "Coralogix alert template"),
        ],
        "/trust": [
            ("on", "enable trust mode (skip approval prompts)"),
            ("off", "disable trust mode"),
        ],
        "/verbose": [
            ("on", "enable verbose logging"),
            ("off", "disable verbose logging"),
        ],
    }

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
            for alias in sorted(_BARE_COMMAND_ALIASES):
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
            sub_prefix = "" if trailing_space or len(parts) < 2 else parts[1].lower()

            # File-path completion for commands that take a path argument
            if cmd_name in ("/investigate", "/save"):
                typed = sub_prefix
                yield from PathCompleter(expanduser=True).get_completions(
                    Document(typed, len(typed)), complete_event
                )
                return

            # Keyword subcommand completion
            for sub, meta in self._SUBCOMMANDS.get(cmd_name, []):
                if sub.startswith(sub_prefix):
                    yield Completion(
                        sub,
                        start_position=-len(sub_prefix),
                        display=sub,
                        display_meta=meta,
                    )


def _build_prompt_session() -> PromptSession[str]:
    return PromptSession(
        completer=ShellCompleter(),
        complete_while_typing=True,
        reserve_space_for_menu=8,
        history=load_prompt_history(),
        key_bindings=_build_prompt_key_bindings(),
        style=_build_prompt_style(),
    )


def _build_prompt_key_bindings() -> KeyBindings:
    bindings = KeyBindings()

    @bindings.add("tab")
    def _tab_complete(event: object) -> None:
        buff = event.current_buffer  # type: ignore[attr-defined]
        if buff.complete_state:
            buff.complete_next()
        else:
            buff.start_completion(select_first=True)

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


def _run_new_alert(text: str, session: ReplSession, console: Console) -> None:
    """Dispatch a free-text alert description to the streaming pipeline."""
    from app.cli.investigation import run_investigation_for_session

    try:
        final_state = run_investigation_for_session(
            alert_text=text,
            context_overrides=session.accumulated_context or None,
        )
    except KeyboardInterrupt:
        console.print("[yellow]investigation cancelled.[/yellow]")
        session.record("alert", text, ok=False)
        return
    except OpenSREError as exc:
        console.print(f"[red]investigation failed:[/red] {escape(str(exc))}")
        if exc.suggestion:
            console.print(f"[yellow]suggestion:[/yellow] {escape(exc.suggestion)}")
        session.record("alert", text, ok=False)
        return
    except Exception as exc:  # noqa: BLE001
        console.print(f"[red]investigation failed:[/red] {escape(str(exc))}")
        session.record("alert", text, ok=False)
        return

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
        text = await prompt.prompt_async(ANSI(f"{PROMPT_ACCENT_ANSI}› {ANSI_RESET}"))
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
        session.record("slash", cmd_text)
        return dispatch_slash(cmd_text, session, console)

    if kind == "cli_help":
        answer_cli_help(text, session, console)
        session.record("cli_help", text)
        return True

    if kind == "cli_agent":
        if execute_cli_actions(text, session, console):
            return True
        answer_cli_agent(text, session, console)
        session.record("cli_agent", text)
        return True

    if kind == "new_alert":
        _run_new_alert(text, session, console)
        return True

    # follow_up — grounded answer against session.last_state
    answer_follow_up(text, session, console)
    session.record("follow_up", text)
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
                session.record("slash", cmd_text)
                if not dispatch_slash(cmd_text, session, console):
                    return 0
            elif kind == "cli_help":
                answer_cli_help(stripped, session, console)
                session.record("cli_help", stripped)
            elif kind == "cli_agent":
                if not execute_cli_actions(stripped, session, console):
                    answer_cli_agent(stripped, session, console)
                    session.record("cli_agent", stripped)
            elif kind == "new_alert":
                _run_new_alert(stripped, session, console)
            else:
                answer_follow_up(stripped, session, console)
                session.record("follow_up", stripped)

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
