"""Domain-specific table renderers for the interactive shell.

Concrete renderers for integrations, models, tools, and planned-actions output.
All rendering is delegated to the REPL TTY helpers in :mod:`rendering`.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from rich.console import Console
from rich.markup import escape
from rich.text import Text

from app.cli.interactive_shell.routing.handle_message_with_agent.orchestration.interaction_models import (
    PlannedAction,
)
from app.cli.interactive_shell.ui.provider import resolve_provider_models
from app.cli.interactive_shell.ui.rendering import (
    _prepare_tty_for_rich,
    print_repl_table,
    repl_print,
    repl_table,
)
from app.cli.interactive_shell.ui.theme import (
    BOLD_BRAND,
    DIM,
    ERROR,
    HIGHLIGHT,
    WARNING,
)

if TYPE_CHECKING:
    from app.cli.interactive_shell.config.tool_catalog import ToolCatalogEntry

# MCP-type services are also rendered under `/mcp list` for focused MCP actions.
MCP_INTEGRATION_SERVICES = frozenset({"github", "openclaw"})


def status_style(status: str) -> str:
    return {
        "ok": HIGHLIGHT,
        "configured": HIGHLIGHT,
        "missing": DIM,
        "failed": WARNING,
        "error": ERROR,
    }.get(status, DIM)


# ---------------------------------------------------------------------------
# Generic table abstraction
# ---------------------------------------------------------------------------


@dataclass
class ColumnDef:
    """Declarative column spec for ``render_table``."""

    header: str
    style: str = ""
    no_wrap: bool = False
    overflow: str = "fold"
    justify: str = "left"
    flex: bool = False  # auto-sizes to fill remaining terminal width


def render_table(
    console: Console,
    title: str,
    columns: list[ColumnDef],
    rows: list[tuple[str | Text, ...]],
    *,
    title_style: str = BOLD_BRAND,
    show_lines: bool = False,
) -> None:
    """TTY-safe generic table renderer.

    Handles: TTY prep, repl_table creation, column wiring, auto-escaping
    string cells, and print_repl_table. Flex columns share remaining width
    after fixed columns claim their budget.
    """
    width = _prepare_tty_for_rich(console)
    flex_count = sum(1 for c in columns if c.flex)
    flex_width = 20
    if flex_count:
        fixed_budget = sum(14 for c in columns if not c.flex)
        flex_width = max(20, (width - fixed_budget) // flex_count)

    table = repl_table(title=f"{title}\n", title_style=title_style, show_lines=show_lines)
    for col in columns:
        col_kwargs: dict[str, Any] = {
            "no_wrap": col.no_wrap,
            "overflow": col.overflow,
            "justify": col.justify,
        }
        if col.style:
            col_kwargs["style"] = col.style
        if col.flex:
            col_kwargs["max_width"] = flex_width
        table.add_column(col.header, **col_kwargs)
    for row in rows:
        table.add_row(*(escape(v) if isinstance(v, str) else v for v in row))
    print_repl_table(console, table, width=width)


# ---------------------------------------------------------------------------
# Concrete table renderers
# ---------------------------------------------------------------------------

_INTEGRATION_COLS: list[ColumnDef] = [
    ColumnDef("service", style="bold", no_wrap=True),
    ColumnDef("source", style=DIM, no_wrap=True),
    ColumnDef("status", no_wrap=True),
    ColumnDef("detail", style=DIM, flex=True),
]

_MODEL_COLS: list[ColumnDef] = [
    ColumnDef("provider", style="bold", no_wrap=True),
    ColumnDef("reasoning model"),
    ColumnDef("toolcall model"),
]

_TOOL_COLS: list[ColumnDef] = [
    ColumnDef("tool", style="bold", no_wrap=True),
    ColumnDef("surfaces", style=DIM, no_wrap=True),
    ColumnDef("params", style=DIM),
    ColumnDef("description", flex=True),
]


def _integration_row(r: dict[str, str]) -> tuple[str | Text, ...]:
    st = r.get("status", "?")
    return (
        r.get("service", "?"),
        r.get("source", "?"),
        Text(st, style=status_style(st)),
        r.get("detail", ""),
    )


def render_integrations_table(console: Console, results: list[dict[str, str]]) -> None:
    rows = sorted(results, key=lambda r: r.get("service", ""))
    if not rows:
        repl_print(
            console, f"[{DIM}]no integrations configured.  try `opensre onboard` to add one.[/]"
        )
        return
    render_table(console, "Integrations", _INTEGRATION_COLS, [_integration_row(r) for r in rows])


def render_mcp_table(console: Console, results: list[dict[str, str]]) -> None:
    rows = sorted(
        (r for r in results if r.get("service") in MCP_INTEGRATION_SERVICES),
        key=lambda r: r.get("service", ""),
    )
    if not rows:
        repl_print(console, f"[{DIM}]no MCP servers configured.[/]")
        return
    render_table(console, "MCP servers", _INTEGRATION_COLS, [_integration_row(r) for r in rows])


def render_models_table(console: Console, settings: Any) -> None:
    if settings is None:
        repl_print(console, f"[{ERROR}]LLM settings unavailable[/] — check provider env vars.")
        return
    provider = str(getattr(settings, "provider", "unknown"))
    reasoning_model, toolcall_model = resolve_provider_models(settings, provider)
    render_table(
        console,
        "LLM connection",
        _MODEL_COLS,
        [(provider, reasoning_model, toolcall_model)],
    )


def render_tools_table(console: Console, entries: list[ToolCatalogEntry]) -> None:
    if not entries:
        repl_print(console, f"[{DIM}]no tools registered.[/]")
        return
    render_table(
        console,
        "Tools",
        _TOOL_COLS,
        [
            (
                entry.name,
                ", ".join(entry.surfaces),
                entry.input_schema_summary,
                entry.description or "-",
            )
            for entry in entries
        ],
        show_lines=True,
    )


def print_command_output(console: Console, output: str, *, style: str | None = None) -> None:
    if not output:
        return
    text = output.rstrip()
    repl_print(console, Text(text) if style is None else Text(text, style=style))


def print_planned_actions(console: Console, actions: list[PlannedAction]) -> None:
    console.print(f"[{DIM}]Requested actions:[/]")
    for index, action in enumerate(actions, start=1):
        label = {
            "llm_provider": "LLM provider",
            "sample_alert": "sample alert",
            "investigation": "investigation",
            "shell": "shell",
            "slash": "command",
            "synthetic_test": "synthetic test",
            "task_cancel": "cancel task",
            "cli_command": "opensre",
            "implementation": "implementation",
            "assistant_handoff": "assistant handoff",
        }[action.kind]
        console.print(f"[{DIM}]{index}.[/] [{BOLD_BRAND}]{label}[/] {escape(action.content)}")


__all__ = [
    "ColumnDef",
    "MCP_INTEGRATION_SERVICES",
    "print_command_output",
    "print_planned_actions",
    "render_integrations_table",
    "render_mcp_table",
    "render_models_table",
    "render_table",
    "render_tools_table",
    "status_style",
]
