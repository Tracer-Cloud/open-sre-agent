"""Slash commands: session control and status (/status, /reset, /clear, /trust, …)."""

from __future__ import annotations

import os

from rich.console import Console
from rich.markup import escape

from app.cli.interactive_shell.banner import render_banner
from app.cli.interactive_shell.command_registry.types import SlashCommand
from app.cli.interactive_shell.rendering import repl_table
from app.cli.interactive_shell.session import ReplSession
from app.cli.interactive_shell.theme import TERMINAL_ACCENT_BOLD


def _cmd_clear(session: ReplSession, console: Console, args: list[str]) -> bool:  # noqa: ARG001
    console.clear()
    render_banner(console)
    return True


def _cmd_reset(session: ReplSession, console: Console, args: list[str]) -> bool:  # noqa: ARG001
    session.clear()
    console.print("[dim]session state cleared.[/dim]")
    return True


def _cmd_trust(session: ReplSession, console: Console, args: list[str]) -> bool:
    if args and args[0].lower() in ("off", "false", "disable"):
        session.trust_mode = False
        console.print("[dim]trust mode off[/dim]")
    else:
        session.trust_mode = True
        console.print("[yellow]trust mode on[/yellow] — future approval prompts will be skipped")
    return True


def _cmd_status(session: ReplSession, console: Console, args: list[str]) -> bool:  # noqa: ARG001
    from app.cli.interactive_shell.cli_reference import get_cli_reference_cache_stats
    from app.cli.interactive_shell.docs_reference import get_docs_cache_stats

    table = repl_table(title="Session status", title_style=TERMINAL_ACCENT_BOLD, show_header=False)
    table.add_column("key", style="bold")
    table.add_column("value")
    table.add_row("interactions", str(len(session.history)))
    table.add_row("last investigation", "yes" if session.last_state else "none")
    table.add_row("trust mode", "on" if session.trust_mode else "off")
    table.add_row("provider", os.getenv("LLM_PROVIDER", "anthropic"))
    cli_stats = get_cli_reference_cache_stats()
    doc_stats = get_docs_cache_stats()
    table.add_row(
        "grounding cli cache",
        f"hits={cli_stats['hits']} misses={cli_stats['misses']} "
        f"cached={'yes' if cli_stats['cached'] else 'no'}",
    )
    table.add_row(
        "grounding docs cache",
        f"hits={doc_stats['hits']} misses={doc_stats['misses']} "
        f"entries={doc_stats['currsize']}/{doc_stats['maxsize']}",
    )
    acc = session.accumulated_context
    if acc:
        table.add_row("accumulated context", ", ".join(sorted(acc.keys())))
    console.print(table)
    return True


def _cmd_cost(session: ReplSession, console: Console, args: list[str]) -> bool:  # noqa: ARG001
    table = repl_table(title="Session cost", title_style=TERMINAL_ACCENT_BOLD, show_header=False)
    table.add_column("key", style="bold")
    table.add_column("value")
    table.add_row("interactions", str(len(session.history)))

    if session.token_usage:
        inp = session.token_usage.get("input", 0)
        out = session.token_usage.get("output", 0)
        table.add_row("input tokens", f"{inp:,}")
        table.add_row("output tokens", f"{out:,}")
    else:
        table.add_row("token usage", "[dim]not available (LangSmith not wired yet)[/dim]")

    console.print(table)
    return True


def _cmd_verbose(session: ReplSession, console: Console, args: list[str]) -> bool:  # noqa: ARG001
    if args and args[0].lower() in ("off", "false", "0", "disable"):
        os.environ.pop("TRACER_VERBOSE", None)
        console.print("[dim]verbose logging off[/dim]")
    else:
        os.environ["TRACER_VERBOSE"] = "1"
        console.print("[yellow]verbose logging on[/yellow]")
    return True


def _cmd_compact(session: ReplSession, console: Console, args: list[str]) -> bool:  # noqa: ARG001
    before = len(session.history)
    if before > 20:
        session.history = session.history[-20:]
        console.print(f"[dim]compacted: kept last 20 of {before} entries.[/dim]")
    else:
        console.print(f"[dim]nothing to compact ({before} entries, limit is 20).[/dim]")
    return True


def _cmd_context(session: ReplSession, console: Console, args: list[str]) -> bool:  # noqa: ARG001
    if not session.accumulated_context:
        console.print("[dim]no infra context accumulated yet.[/dim]")
        return True

    table = repl_table(
        title="Accumulated context", title_style=TERMINAL_ACCENT_BOLD, show_header=False
    )
    table.add_column("key", style="bold")
    table.add_column("value")
    for k, v in sorted(session.accumulated_context.items()):
        table.add_row(k, escape(str(v)))
    console.print(table)
    return True


_TRUST_FIRST_ARGS: tuple[tuple[str, str], ...] = (
    ("on", "enable trust mode (skip approval prompts)"),
    ("off", "disable trust mode"),
)

_VERBOSE_FIRST_ARGS: tuple[tuple[str, str], ...] = (
    ("on", "enable verbose logging"),
    ("off", "disable verbose logging"),
)

COMMANDS: list[SlashCommand] = [
    SlashCommand("/clear", "clear the screen and re-render the banner", _cmd_clear),
    SlashCommand("/reset", "clear session state (keeps trust mode)", _cmd_reset),
    SlashCommand(
        "/trust",
        "toggle trust mode ('/trust off' to disable)",
        _cmd_trust,
        first_arg_completions=_TRUST_FIRST_ARGS,
    ),
    SlashCommand("/status", "show session status", _cmd_status),
    SlashCommand("/context", "show accumulated infra context", _cmd_context),
    SlashCommand("/cost", "show token usage and session cost", _cmd_cost),
    SlashCommand(
        "/verbose",
        "toggle verbose logging ('/verbose off' to disable)",
        _cmd_verbose,
        first_arg_completions=_VERBOSE_FIRST_ARGS,
    ),
    SlashCommand("/compact", "trim old session history to free memory", _cmd_compact),
]

__all__ = ["COMMANDS"]
