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
from app.cli.interactive_shell.theme import TERMINAL_ACCENT_BOLD, TERMINAL_ERROR


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
    return {
        "ok": "green",
        "configured": "green",
        "missing": "yellow",
        "failed": "red",
    }.get(status, "dim")


# MCP-type services are rendered separately under `/list mcp` so the default
# `/list integrations` view stays focused on alert-source / data integrations.
_MCP_SERVICES = frozenset({"github", "openclaw"})


def render_integrations_table(console: Console, results: list[dict[str, str]]) -> None:
    rows = [r for r in results if r.get("service") not in _MCP_SERVICES]
    if not rows:
        console.print("[dim]no integrations configured.  try `opensre onboard` to add one.[/dim]")
        return
    table = repl_table(title="Integrations", title_style=TERMINAL_ACCENT_BOLD)
    table.add_column("service", style="bold")
    table.add_column("source", style="dim")
    table.add_column("status")
    table.add_column("detail", style="dim", overflow="fold")
    for row in rows:
        st = row.get("status", "unknown")
        table.add_row(
            escape(row.get("service", "?")),
            escape(row.get("source", "?")),
            f"[{status_style(st)}]{escape(st)}[/{status_style(st)}]",
            escape(row.get("detail", "")),
        )
    console.print(table)


def render_mcp_table(console: Console, results: list[dict[str, str]]) -> None:
    rows = [r for r in results if r.get("service") in _MCP_SERVICES]
    if not rows:
        console.print("[dim]no MCP servers configured.[/dim]")
        return
    table = repl_table(title="MCP servers", title_style=TERMINAL_ACCENT_BOLD)
    table.add_column("server", style="bold")
    table.add_column("source", style="dim")
    table.add_column("status")
    table.add_column("detail", style="dim", overflow="fold")
    for row in rows:
        st = row.get("status", "unknown")
        table.add_row(
            escape(row.get("service", "?")),
            escape(row.get("source", "?")),
            f"[{status_style(st)}]{escape(st)}[/{status_style(st)}]",
            escape(row.get("detail", "")),
        )
    console.print(table)


def render_models_table(console: Console, settings: Any) -> None:
    if settings is None:
        console.print(f"[{TERMINAL_ERROR}]LLM settings unavailable[/] — check provider env vars.")
        return
    provider = str(getattr(settings, "provider", "unknown"))
    reasoning_model, toolcall_model = resolve_provider_models(settings, provider)
    table = repl_table(title="LLM connection", title_style=TERMINAL_ACCENT_BOLD, show_header=False)
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
    console.print("[dim]Requested actions:[/dim]")
    for index, action in enumerate(actions, start=1):
        label = {
            "llm_provider": "LLM provider",
            "sample_alert": "sample alert",
            "shell": "shell",
            "slash": "command",
            "synthetic_test": "synthetic test",
        }[action.kind]
        console.print(
            f"[dim]{index}.[/dim] [{TERMINAL_ACCENT_BOLD}]{label}[/] {escape(action.content)}"
        )


__all__ = [
    "print_command_output",
    "print_planned_actions",
    "repl_table",
    "render_integrations_table",
    "render_mcp_table",
    "render_models_table",
    "status_style",
]
