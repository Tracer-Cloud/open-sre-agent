"""Tool registry and investigation-tool resolution.

Core resolves the tools for a surface and the investigation tool list without
importing the tool packages: ``tools/harness_adapters.py`` installs the real
sources once at boot, read afterwards through ``resolve_*``. There is no
setter — nothing rewrites the sources after boot.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from core.domain.types.tools import ToolSurface
from core.tool import RegisteredTool

if TYPE_CHECKING:
    from core.tool import ToolRegistry

InvestigationToolsFn = Callable[[dict[str, Any]], list[RegisteredTool]]


class _EmptyToolRegistry:
    """Default tool registry that resolves nothing until one is installed."""

    def tools_for_surface(self, _surface: ToolSurface) -> list[RegisteredTool]:
        return []

    def tool_map_for_surface(self, _surface: ToolSurface) -> dict[str, RegisteredTool]:
        return {}


def _default_investigation_tools(_resolved: dict[str, Any]) -> list[RegisteredTool]:
    return []


_EMPTY_REGISTRY: ToolRegistry = _EmptyToolRegistry()


@dataclass(frozen=True)
class ToolSources:
    """The tool registry and investigation-tool resolver, installed once at boot."""

    registry: ToolRegistry = _EMPTY_REGISTRY
    investigation_tools: InvestigationToolsFn = _default_investigation_tools

    def install(self) -> None:
        """Bind these as the process-wide tool sources."""
        global _installed
        _installed = self


_installed: ToolSources | None = None


def resolve_surface_tools(surface: ToolSurface) -> list[RegisteredTool]:
    """Return the installed registry's tools for ``surface`` (empty before boot)."""
    return _installed.registry.tools_for_surface(surface) if _installed is not None else []


def resolve_surface_tool_map(surface: ToolSurface) -> dict[str, RegisteredTool]:
    """Return the installed registry's name→tool map for ``surface`` (empty before boot)."""
    return _installed.registry.tool_map_for_surface(surface) if _installed is not None else {}


def resolve_investigation_tools(resolved_integrations: dict[str, Any]) -> list[RegisteredTool]:
    """Return the investigation tools for ``resolved_integrations`` (empty before boot)."""
    return _installed.investigation_tools(resolved_integrations) if _installed is not None else []


def reset() -> None:
    """Clear the installed tool sources (tests)."""
    global _installed
    _installed = None


__all__ = [
    "InvestigationToolsFn",
    "ToolSources",
    "resolve_investigation_tools",
    "resolve_surface_tool_map",
    "resolve_surface_tools",
]
