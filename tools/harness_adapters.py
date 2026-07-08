"""Wire tools-layer helpers into :mod:`platform.harness_ports`."""

from __future__ import annotations


def register_harness_adapters() -> None:
    from core.tool_framework.registered_tool import RegisteredTool
    from platform.harness_ports import set_investigation_tools_adapter, set_tool_registry_adapters
    from tools.investigation.stages.gather_evidence.tools import get_available_tools
    from tools.registry import get_registered_tool_map, get_registered_tools

    def _surface_tools(surface: str) -> list[RegisteredTool]:
        return get_registered_tools(surface)  # type: ignore[arg-type]

    def _surface_tool_map(surface: str) -> dict[str, RegisteredTool]:
        return get_registered_tool_map(surface)  # type: ignore[arg-type]

    set_tool_registry_adapters(
        get_surface_tools=_surface_tools,
        get_surface_tool_map=_surface_tool_map,
    )
    set_investigation_tools_adapter(get_investigation_tools=get_available_tools)
