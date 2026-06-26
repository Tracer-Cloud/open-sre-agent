"""Slash commands: session diagnostics (/status, /cost, /context)."""

from __future__ import annotations

from rich.console import Console
from rich.markup import escape

from cli.interactive_shell.command_registry.types import SlashCommand
from cli.interactive_shell.runtime import ReplSession
from cli.interactive_shell.token_accounting import format_token_total
from cli.interactive_shell.ui import (
    BOLD_BRAND,
    DIM,
    print_repl_table,
    repl_table,
)
from config.llm_reasoning_effort import display_reasoning_effort


def _status_provider_display() -> str:
    """Render the active LLM provider, flagging a fallback away from configured."""
    from config.config import get_configured_llm_provider, resolve_llm_settings_verbose

    try:
        resolution = resolve_llm_settings_verbose()
    except Exception:
        return get_configured_llm_provider()

    if not resolution.fell_back:
        return resolution.resolved_provider

    from cli.interactive_shell.ui import WARNING

    note = f"fallback from '{resolution.configured_provider}'"
    if resolution.missing_key_env:
        note += f": {resolution.missing_key_env} not set"
    return f"{resolution.resolved_provider} [{WARNING}]({note})[/]"


def _cmd_status(session: ReplSession, console: Console, _args: list[str]) -> bool:
    import cli.interactive_shell.references.cli_reference  # noqa: F401
    import cli.interactive_shell.references.docs_reference  # noqa: F401
    from cli.interactive_shell.references.grounding_diagnostics import iter_grounding_sources

    table = repl_table(title="Session status\n", title_style=BOLD_BRAND, show_header=False)
    table.add_column("key", style="bold")
    table.add_column("value")
    table.add_row("interactions", str(len(session.history)))

    if session.incoming_alerts:
        from cli.interactive_shell.alert_renderer import time_ago

        most_recent = session.incoming_alerts[-1]
        age_str = time_ago(most_recent.received_at)
        table.add_row("incoming alerts", f"{len(session.incoming_alerts)} (last {age_str})")
    else:
        table.add_row("incoming alerts", "0")

    table.add_row("last investigation", "yes" if session.last_state else "none")
    table.add_row("trust mode", "on" if session.trust_mode else "off")
    table.add_row("reasoning effort", display_reasoning_effort(session.reasoning_effort))
    table.add_row("provider", _status_provider_display())
    for source in iter_grounding_sources():
        stats = source.stats_fn()
        table.add_row(f"grounding {source.name} cache", source.format_fn(stats))
    acc = session.accumulated_context
    if acc:
        table.add_row("accumulated context", ", ".join(sorted(acc.keys())))
    print_repl_table(console, table)
    return True


def _cmd_cost(session: ReplSession, console: Console, _args: list[str]) -> bool:
    title = "Session cost"
    if session.token_usage_has_estimates:
        title = "Session cost (includes estimates)"
    table = repl_table(title=f"{title}\n", title_style=BOLD_BRAND, show_header=False)
    table.add_column("key", style="bold")
    table.add_column("value")
    table.add_row("history entries", str(len(session.history)))
    if session.llm_call_count:
        table.add_row("llm calls", str(session.llm_call_count))

    if session.token_usage:
        for direction in ("input", "output"):
            label, value = format_token_total(session, direction=direction)
            table.add_row(label, value)
    else:
        table.add_row("token usage", f"[{DIM}]no LLM usage recorded yet[/]")

    print_repl_table(console, table)
    return True


def _cmd_context(session: ReplSession, console: Console, _args: list[str]) -> bool:
    if not session.accumulated_context:
        console.print(f"[{DIM}]no infra context accumulated yet.[/]")
        return True

    table = repl_table(title="Accumulated context\n", title_style=BOLD_BRAND, show_header=False)
    table.add_column("key", style="bold")
    table.add_column("value")
    for k, v in sorted(session.accumulated_context.items()):
        table.add_row(k, escape(str(v)))
    print_repl_table(console, table)
    return True


COMMANDS: list[SlashCommand] = [
    SlashCommand("/status", "Show session status.", _cmd_status),
    SlashCommand("/cost", "Show token usage and session cost.", _cmd_cost),
    SlashCommand("/context", "Show accumulated infra context.", _cmd_context),
]

__all__ = ["COMMANDS"]
