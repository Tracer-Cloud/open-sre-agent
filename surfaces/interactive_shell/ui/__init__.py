from __future__ import annotations

from typing import TYPE_CHECKING, Any

from platform.terminal.theme import (
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
from surfaces.interactive_shell.ui.banner import render_banner, render_ready_box
from surfaces.interactive_shell.ui.components import (
    print_valid_choice_list,
    repl_choose_one,
    repl_section_break,
    repl_tty_interactive,
)
from surfaces.interactive_shell.ui.components.rendering import (
    print_repl_json,
    print_repl_table,
    refresh_welcome_poster,
    repl_print,
    repl_table,
)

if TYPE_CHECKING:
    from surfaces.interactive_shell.ui.agents.agents_view import (
        _build_agents_table,
        render_agents_table,
    )
    from surfaces.interactive_shell.ui.streaming import (
        STREAM_LABEL_ANSWER,
        STREAM_LABEL_ASSISTANT,
        stream_to_console,
    )
    from surfaces.interactive_shell.ui.tables import (
        MCP_INTEGRATION_SERVICES,
        ColumnDef,
        print_command_output,
        render_integrations_table,
        render_mcp_table,
        render_models_table,
        render_table,
        render_tools_table,
        resolve_provider_models,
    )

# Heavy re-exports resolved lazily so importing ``ui`` (done on every REPL boot
# via the prompt/completion path) does not force the table + streaming stack.
_LAZY_SUBMODULE_EXPORTS: dict[str, str] = {
    "_build_agents_table": "surfaces.interactive_shell.ui.agents",
    "render_agents_table": "surfaces.interactive_shell.ui.agents",
    "STREAM_LABEL_ANSWER": "surfaces.interactive_shell.ui.streaming",
    "STREAM_LABEL_ASSISTANT": "surfaces.interactive_shell.ui.streaming",
    "stream_to_console": "surfaces.interactive_shell.ui.streaming",
    "MCP_INTEGRATION_SERVICES": "surfaces.interactive_shell.ui.tables",
    "ColumnDef": "surfaces.interactive_shell.ui.tables",
    "print_command_output": "surfaces.interactive_shell.ui.tables",
    "render_integrations_table": "surfaces.interactive_shell.ui.tables",
    "render_mcp_table": "surfaces.interactive_shell.ui.tables",
    "render_models_table": "surfaces.interactive_shell.ui.tables",
    "render_table": "surfaces.interactive_shell.ui.tables",
    "render_tools_table": "surfaces.interactive_shell.ui.tables",
    "resolve_provider_models": "surfaces.interactive_shell.ui.tables",
}


def __getattr__(name: str) -> Any:
    module_path = _LAZY_SUBMODULE_EXPORTS.get(name)
    if module_path is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    import importlib

    return getattr(importlib.import_module(module_path), name)


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
    "print_repl_json",
    "print_repl_table",
    "render_agents_table",
    "refresh_welcome_poster",
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
