"""Command-output tables, provider metadata, and tool catalog helpers."""

from surfaces.interactive_shell.ui.tables.provider import (
    detect_provider_model,
    resolve_provider_models,
)
from surfaces.interactive_shell.ui.tables.tables import (
    MCP_INTEGRATION_SERVICES,
    ColumnDef,
    print_command_output,
    render_integrations_table,
    render_mcp_table,
    render_models_table,
    render_table,
    render_tools_table,
)
from surfaces.interactive_shell.ui.tables.tool_catalog import (
    ToolCatalogEntry,
    build_tool_catalog,
    format_tool_catalog_text,
)

__all__ = [
    "MCP_INTEGRATION_SERVICES",
    "ColumnDef",
    "ToolCatalogEntry",
    "build_tool_catalog",
    "detect_provider_model",
    "format_tool_catalog_text",
    "print_command_output",
    "render_integrations_table",
    "render_mcp_table",
    "render_models_table",
    "render_table",
    "render_tools_table",
    "resolve_provider_models",
]
