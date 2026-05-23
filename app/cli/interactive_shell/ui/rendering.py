"""Rich table and console output helpers for the interactive shell."""

from __future__ import annotations

import io
import shutil
import sys
from contextvars import ContextVar
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from rich import box
from rich.console import Console
from rich.markup import escape
from rich.table import Table
from rich.text import Text

from app.cli.interactive_shell.routing.handle_message_with_agent.orchestration.interaction_models import (
    PlannedAction,
)
from app.cli.interactive_shell.ui.banner import resolve_provider_models
from app.cli.interactive_shell.ui.theme import (
    BOLD_BRAND,
    DIM,
    ERROR,
    HIGHLIGHT,
    WARNING,
)

if TYPE_CHECKING:
    from app.cli.interactive_shell.config.tool_catalog import ToolCatalogEntry


def repl_table(**kwargs: Any) -> Table:
    """Minimal outer borders — closer to Claude Code than full ASCII grids."""
    opts: dict[str, Any] = {
        "box": box.MINIMAL_HEAVY_HEAD,
        "show_edge": False,
        "pad_edge": False,
        "title_justify": "left",
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
_REPL_OUTPUT_PREPARED = ContextVar("_REPL_OUTPUT_PREPARED", default=False)


def _repl_output_already_prepared() -> bool:
    """Whether current call stack already prepared the TTY for Rich output."""
    return _REPL_OUTPUT_PREPARED.get()


def _console_print_prepared(console: Console, *objects: Any, **kwargs: Any) -> None:
    token = _REPL_OUTPUT_PREPARED.set(True)
    try:
        console.print(*objects, **kwargs)
    finally:
        _REPL_OUTPUT_PREPARED.reset(token)


def _repl_table_width(console: Console) -> int:
    """Best-effort terminal width for Rich tables after inline menu I/O."""
    term_cols = shutil.get_terminal_size(fallback=(80, 24)).columns
    # Keep one safety column to avoid right-edge auto-wrap artifacts in some
    # terminals (first-char clipping / duplicate right border when a row lands
    # exactly on the terminal width).
    return max(40, min(console.width, term_cols) - 1)


def _prepare_tty_for_rich(console: Console) -> int:
    """Return the width Rich should render at.

    prepare_repl_output_line() (which writes \\r\\n) is intentionally NOT called
    here. Under patch_stdout(raw=True), that extra newline causes the bottom
    toolbar text to flush into the output stream before the table renders. Slash
    commands start after the user presses Enter, so the cursor is already on a
    fresh line; no extra line-feed is needed.
    """
    return _repl_table_width(console)


def print_repl_table(console: Console, table: Table, *, width: int | None = None) -> None:
    """Print a Rich table using REPL-safe TTY width.

    When the console writes to sys.stdout (the real REPL path), tables are
    rendered into a string buffer first and written in a single sys.stdout.write
    call with explicit \\r\\n line endings. This prevents the diagonal-render
    artifact that occurs under prompt_toolkit's patch_stdout: each table row is
    a separate Rich write, and if the terminal or proxy does not convert \\n to
    \\r\\n, every row starts where the previous one ended instead of column zero.

    When the console writes to a non-TTY stdout (piped output) or to a
    different file (e.g. a StringIO in tests), the normal console.print path
    is used — preserving the caller's color_system and avoiding ANSI pollution
    in piped output.
    """
    leading_blank = width is None
    width = width if width is not None else _prepare_tty_for_rich(console)
    if console.file is sys.stdout and sys.stdout.isatty():
        buf = io.StringIO()
        buf_console = Console(
            file=buf,
            force_terminal=True,
            highlight=False,
            width=width,
        )
        buf_console.print(table)
        rendered = buf.getvalue()
        # Normalise to \r\n so each row starts at column zero even when ONLCR is
        # disabled (raw-mode terminal under patch_stdout). Strip pre-existing \r\n
        # first so partial Windows line-endings in cell content don't prevent the
        # remaining bare \n chars from being converted.
        rendered = rendered.replace("\r\n", "\n").replace("\n", "\r\n")
        # Prepend blank line as part of the same write to avoid a separate
        # patch_stdout proxy flush that can trigger a toolbar DSR query and
        # leave stale CPR bytes in stdin for the next prompt.
        if leading_blank:
            rendered = "\r\n" + rendered
        token = _REPL_OUTPUT_PREPARED.set(True)
        try:
            sys.stdout.write(rendered)
            sys.stdout.flush()
        finally:
            _REPL_OUTPUT_PREPARED.reset(token)
    else:
        if leading_blank:
            _console_print_prepared(console)
        _console_print_prepared(console, table, width=width)


def repl_print(console: Console, *objects: Any, **kwargs: Any) -> None:
    """Print via Rich after resetting the TTY column (inline-menu safe)."""
    from app.cli.interactive_shell.ui.choice_menu import prepare_repl_output_line

    prepare_repl_output_line()
    _console_print_prepared(console, *objects, **kwargs)


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
    rows = [r for r in results if r.get("service") not in MCP_INTEGRATION_SERVICES]
    if not rows:
        repl_print(
            console, f"[{DIM}]no integrations configured.  try `opensre onboard` to add one.[/]"
        )
        return
    render_table(console, "Integrations", _INTEGRATION_COLS, [_integration_row(r) for r in rows])


def render_mcp_table(console: Console, results: list[dict[str, str]]) -> None:
    rows = [r for r in results if r.get("service") in MCP_INTEGRATION_SERVICES]
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
    "_repl_table_width",
    "print_command_output",
    "print_planned_actions",
    "print_repl_table",
    "render_table",
    "repl_print",
    "repl_table",
    "render_integrations_table",
    "render_tools_table",
    "render_mcp_table",
    "render_models_table",
    "status_style",
]
