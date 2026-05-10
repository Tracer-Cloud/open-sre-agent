"""Rich table and console output helpers for the interactive shell."""

from __future__ import annotations

from typing import Any

from rich import box
from rich.console import Console
from rich.markup import escape
from rich.table import Table
from rich.text import Text

from app.cli.interactive_shell.banner import resolve_provider_models
from app.cli.interactive_shell.interaction_models import PlannedAction
from app.cli.interactive_shell.theme import (
    BOLD_BRAND,
    DIM,
    ERROR,
    HIGHLIGHT,
    WARNING,
)


def repl_table(**kwargs: Any) -> Table:
    """Minimal outer borders — closer to Claude Code than full ASCII grids."""
    opts: dict[str, Any] = {
        "box": box.MINIMAL_HEAVY_HEAD,
        "show_edge": False,
        "pad_edge": False,
    }
    opts.update(kwargs)
    return Table(**opts)


def status_style(status: str) -> str:
    # Semantic rule: a missing/unconfigured integration is the default
    # state (DIM), while a previously-configured integration that is now
    # broken is a WARNING. Hard failures escalate to ERROR.
    return {
        "ok": HIGHLIGHT,
        "configured": HIGHLIGHT,
        "missing": DIM,
        "failed": WARNING,
        "error": ERROR,
    }.get(status, DIM)


# MCP-type services are rendered separately under `/list mcp` so the default
# `/list integrations` view stays focused on alert-source / data integrations.
MCP_INTEGRATION_SERVICES = frozenset({"github", "openclaw"})


def render_integrations_table(console: Console, results: list[dict[str, str]]) -> None:
    rows = [
        r
        for r in results
        if r.get("service") not in MCP_INTEGRATION_SERVICES and r.get("status") != "missing"
    ]
    if not rows:
        console.print(f"[{DIM}]no integrations configured.  try `opensre onboard` to add one.[/]")
        return
    table = repl_table(title="Integrations", title_style=BOLD_BRAND)
    table.add_column("service", style="bold")
    table.add_column("source", style=DIM)
    table.add_column("status")
    table.add_column("detail", style=DIM, overflow="fold")
    for row in rows:
        st = row.get("status", "unknown")
        table.add_row(
            escape(row.get("service", "?")),
            escape(row.get("source", "?")),
            f"[{status_style(st)}]{escape(st)}[/]",
            escape(row.get("detail", "")),
        )
    console.print(table)


def render_mcp_table(console: Console, results: list[dict[str, str]]) -> None:
    rows = [r for r in results if r.get("service") in MCP_INTEGRATION_SERVICES]
    if not rows:
        console.print(f"[{DIM}]no MCP servers configured.[/]")
        return
    table = repl_table(title="MCP servers", title_style=BOLD_BRAND)
    table.add_column("server", style="bold")
    table.add_column("source", style=DIM)
    table.add_column("status")
    table.add_column("detail", style=DIM, overflow="fold")
    for row in rows:
        st = row.get("status", "unknown")
        table.add_row(
            escape(row.get("service", "?")),
            escape(row.get("source", "?")),
            f"[{status_style(st)}]{escape(st)}[/]",
            escape(row.get("detail", "")),
        )
    console.print(table)


def render_models_table(console: Console, settings: Any) -> None:
    if settings is None:
        console.print(f"[{ERROR}]LLM settings unavailable[/] — check provider env vars.")
        return
    provider = str(getattr(settings, "provider", "unknown"))
    reasoning_model, toolcall_model = resolve_provider_models(settings, provider)
    table = repl_table(title="LLM connection", title_style=BOLD_BRAND, show_header=False)
    table.add_column("key", style="bold")
    table.add_column("value")
    table.add_row("provider", provider)
    table.add_row("reasoning model", reasoning_model)
    table.add_row("toolcall model", toolcall_model)
    console.print(table)


def print_command_output(console: Console, output: str, *, style: str | None = None) -> None:
    if not output:
        return
    text = output.rstrip()
    console.print(Text(text) if style is None else Text(text, style=style))


def print_planned_actions(console: Console, actions: list[PlannedAction]) -> None:
    console.print(f"[{DIM}]Requested actions:[/]")
    for index, action in enumerate(actions, start=1):
        label = {
            "llm_provider": "LLM provider",
            "sample_alert": "sample alert",
            "shell": "shell",
            "slash": "command",
            "synthetic_test": "synthetic test",
            "task_cancel": "cancel task",
            "cli_command": "opensre",
            "implementation": "implementation",
        }[action.kind]
        console.print(f"[{DIM}]{index}.[/] [{BOLD_BRAND}]{label}[/] {escape(action.content)}")


__all__ = [
    "MCP_INTEGRATION_SERVICES",
    "print_command_output",
    "print_planned_actions",
    "repl_table",
    "render_integrations_table",
    "render_mcp_table",
    "render_models_table",
    "status_style",
]
