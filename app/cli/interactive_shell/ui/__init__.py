from __future__ import annotations

from typing import Any

from app.cli.interactive_shell.ui.banner import (
    render_banner,
    render_ready_box,
    resolve_provider_models,
)
from app.cli.interactive_shell.ui.choice_menu import (
    print_valid_choice_list,
    repl_choose_one,
    repl_section_break,
    repl_tty_interactive,
)
from app.cli.interactive_shell.ui.rendering import (
    MCP_INTEGRATION_SERVICES,
    ColumnDef,
    print_command_output,
    print_planned_actions,
    print_repl_json,
    print_repl_table,
    render_integrations_table,
    render_mcp_table,
    render_models_table,
    render_table,
    render_tools_table,
    repl_print,
    repl_table,
)
from app.cli.interactive_shell.ui.streaming import (
    STREAM_LABEL_ANSWER,
    STREAM_LABEL_ASSISTANT,
    stream_to_console,
)
from app.cli.interactive_shell.ui.theme import (
    ANSI_DIM,
    ANSI_RESET,
    BG,
    BOLD_BRAND,
    DIM,
    DIM_COUNTER_ANSI,
    ERROR,
    HIGHLIGHT,
    MARKDOWN_THEME,
    PROMPT_ACCENT_ANSI,
    PROMPT_FRAME_ANSI,
    SECONDARY,
    TEXT,
    WARNING,
)


def __getattr__(name: str) -> Any:
    if name in {"_build_agents_table", "render_agents_table"}:
        from app.cli.interactive_shell.ui import agents_view

        return getattr(agents_view, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


__all__ = [
    "ANSI_DIM",
    "ANSI_RESET",
    "BG",
    "BOLD_BRAND",
    "ColumnDef",
    "DIM",
    "DIM_COUNTER_ANSI",
    "ERROR",
    "HIGHLIGHT",
    "MCP_INTEGRATION_SERVICES",
    "MARKDOWN_THEME",
    "PROMPT_ACCENT_ANSI",
    "PROMPT_FRAME_ANSI",
    "SECONDARY",
    "STREAM_LABEL_ANSWER",
    "STREAM_LABEL_ASSISTANT",
    "TEXT",
    "WARNING",
    "_build_agents_table",
    "print_valid_choice_list",
    "print_command_output",
    "print_planned_actions",
    "print_repl_json",
    "print_repl_table",
    "render_agents_table",
    "render_banner",
    "render_ready_box",
    "render_integrations_table",
    "render_mcp_table",
    "render_models_table",
    "render_table",
    "render_tools_table",
    "repl_choose_one",
    "repl_print",
    "repl_section_break",
    "repl_table",
    "repl_tty_interactive",
    "resolve_provider_models",
    "stream_to_console",
]
