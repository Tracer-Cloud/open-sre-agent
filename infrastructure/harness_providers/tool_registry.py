"""Tool registry and investigation-tool resolution.

Core resolves the tools for a surface and the investigation tool list without
importing the tool packages: ``tools/harness_adapters.py`` injects a real
registry at boot.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING, Any

from core.domain.types.tools import ToolSurface
from core.tool import RegisteredTool

if TYPE_CHECKING:
    from core.tool import ToolRegistry

InvestigationToolsFn = Callable[[dict[str, Any]], list[RegisteredTool]]


class _EmptyToolRegistry:
    """Default tool registry that resolves nothing until one is injected."""

    def tools_for_surface(self, _surface: ToolSurface) -> list[RegisteredTool]:
        return []

    def tool_map_for_surface(self, _surface: ToolSurface) -> dict[str, RegisteredTool]:
        return {}


def _default_investigation_tools(_resolved: dict[str, Any]) -> list[RegisteredTool]:
    return []


_tool_registry: ToolRegistry = _EmptyToolRegistry()
_get_investigation_tools: InvestigationToolsFn = _default_investigation_tools


def get_surface_tools(surface: ToolSurface) -> list[RegisteredTool]:
    return _tool_registry.tools_for_surface(surface)


def get_surface_tool_map(surface: ToolSurface) -> dict[str, RegisteredTool]:
    return _tool_registry.tool_map_for_surface(surface)


def get_investigation_tools(resolved_integrations: dict[str, Any]) -> list[RegisteredTool]:
    return _get_investigation_tools(resolved_integrations)


def set_tool_registry(registry: ToolRegistry) -> None:
    global _tool_registry
    _tool_registry = registry


def set_investigation_tools_adapter(
    get_investigation_tools: InvestigationToolsFn | None = None,
) -> None:
    global _get_investigation_tools
    if get_investigation_tools is not None:
        _get_investigation_tools = get_investigation_tools


def reset() -> None:
    """Restore the empty registry and noop investigation tools (tests)."""
    set_tool_registry(_EmptyToolRegistry())
    set_investigation_tools_adapter(get_investigation_tools=_default_investigation_tools)


__all__ = [
    "InvestigationToolsFn",
    "get_investigation_tools",
    "get_surface_tool_map",
    "get_surface_tools",
    "set_investigation_tools_adapter",
    "set_tool_registry",
]
