from __future__ import annotations

from typing import TYPE_CHECKING, Any

from cli.interactive_shell.ui.banner import (
    render_banner,
    render_ready_box,
)
from cli.interactive_shell.ui.choice_menu import (
    print_valid_choice_list,
    repl_choose_one,
    repl_section_break,
    repl_tty_interactive,
)
from cli.interactive_shell.ui.provider import resolve_provider_models
from cli.interactive_shell.ui.rendering import (
    print_repl_json,
    print_repl_table,
    repl_print,
    repl_table,
)
from cli.interactive_shell.ui.streaming import (
    STREAM_LABEL_ANSWER,
    STREAM_LABEL_ASSISTANT,
    stream_to_console,
)
from cli.interactive_shell.ui.tables import (
    MCP_INTEGRATION_SERVICES,
    ColumnDef,
    print_command_output,
    print_planned_actions,
    render_integrations_table,
    render_mcp_table,
    render_models_table,
    render_table,
    render_tools_table,
)
from cli.interactive_shell.ui.theme import (
    ANSI_DIM,
    ANSI_RESET,
    BG,
    BOLD_BRAND,
    DEVICE_CODE,
    DEVICE_CODE_ANSI,
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

if TYPE_CHECKING:
    # ``_build_agents_table`` and ``render_agents_table`` are PEP 562 lazy module
    # attributes resolved by ``__getattr__`` below (loaded from ``agents_view`` only
    # on first access so collectors don't pull in Rich). Declaring them here makes
    # them visible to static analyzers that can't follow ``__getattr__`` (CodeQL
    # ``py/undefined-export``, ruff F822) without eagerly importing the module.
    from cli.interactive_shell.ui.agents_view import (
        _build_agents_table,
        render_agents_table,
    )


def __getattr__(name: str) -> Any:
    if name in {"_build_agents_table", "render_agents_table"}:
        from cli.interactive_shell.ui import agents_view

        return getattr(agents_view, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


__all__ = [
    "ANSI_DIM",
    "ANSI_RESET",
    "BG",
    "BOLD_BRAND",
    "ColumnDef",
    "DEVICE_CODE",
    "DEVICE_CODE_ANSI",
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
