"""Slash commands: session diagnostics (/status, /cost, /context)."""

from __future__ import annotations

from rich.console import Console
from rich.markup import escape

from config.llm_reasoning_effort import display_reasoning_effort
from core.agent_harness.accounting.token_accounting import format_token_total
from core.agent_harness.session.terminal_access import trust_mode_enabled
from surfaces.interactive_shell.command_registry.types import SlashCommand
from surfaces.interactive_shell.grounding.cli_reference import session_cli_reference
from surfaces.interactive_shell.runtime import Session
from surfaces.interactive_shell.ui import (
    BOLD_BRAND,
    DIM,
    print_repl_table,
    repl_table,
)


def _status_provider_display() -> str:
    """Render the active LLM provider, flagging a fallback away from configured."""
    from config.config import get_configured_llm_provider, resolve_llm_settings_verbose

    try:
        resolution = resolve_llm_settings_verbose()
    except Exception:
        return get_configured_llm_provider()

    if not resolution.fell_back:
        return resolution.resolved_provider

    from surfaces.interactive_shell.ui import WARNING

    note = f"fallback from '{resolution.configured_provider}'"
    if resolution.missing_key_env:
        note += f": {resolution.missing_key_env} not set"
    return f"{resolution.resolved_provider} [{WARNING}]({note})[/]"


def _incoming_alerts_status(session: Session) -> tuple[str, str]:
    """Return (label, value) for the incoming-alerts row; headless sessions have no inbox."""
    alerts = getattr(session, "alerts", None)
    if alerts is None:
        return "incoming alerts", "0"
    most_recent = alerts.most_recent
    if most_recent is not None:
        from surfaces.interactive_shell.ui.alerts import time_ago

        age_str = time_ago(most_recent.received_at)
        return "incoming alerts", f"{len(alerts.entries)} (last {age_str})"
    return "incoming alerts", "0"


def _cmd_status(session: Session, console: Console, _args: list[str]) -> bool:
    table = repl_table(title="Session status\n", title_style=BOLD_BRAND, show_header=False)
    table.add_column("key", style="bold")
    table.add_column("value")
    table.add_row("interactions", str(len(session.history)))

    alert_key, alert_value = _incoming_alerts_status(session)
    table.add_row(alert_key, alert_value)

    table.add_row("last investigation", "yes" if session.last_state else "none")
    table.add_row("trust mode", "on" if trust_mode_enabled(session) else "off")
    table.add_row("reasoning effort", display_reasoning_effort(session.reasoning_effort))
    table.add_row("provider", _status_provider_display())
    table.add_row(
        "grounding cli cache",
        session_cli_reference(session).stats().render(),
    )
    for source in session.grounding.iter_sources():
        table.add_row(f"grounding {source.name} cache", source.stats_fn().render())
    acc = session.accumulated_context
    if acc:
        table.add_row("accumulated context", ", ".join(sorted(acc.keys())))
    print_repl_table(console, table)
    return True


def _cmd_cost(session: Session, console: Console, _args: list[str]) -> bool:
    title = "Session cost"
    if session.tokens.has_estimates:
        title = "Session cost (includes estimates)"
    table = repl_table(title=f"{title}\n", title_style=BOLD_BRAND, show_header=False)
    table.add_column("key", style="bold")
    table.add_column("value")
    table.add_row("history entries", str(len(session.history)))
    if session.tokens.call_count:
        table.add_row("llm calls", str(session.tokens.call_count))

    if session.tokens.totals:
        for direction in ("input", "output"):
            label, value = format_token_total(session, direction=direction)
            table.add_row(label, value)
    else:
        table.add_row("token usage", f"[{DIM}]no LLM usage recorded yet[/]")

    print_repl_table(console, table)
    return True


def _cmd_context(session: Session, console: Console, _args: list[str]) -> bool:
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
