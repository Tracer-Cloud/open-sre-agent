"""Wire tools-layer helpers into :mod:`infrastructure.harness_providers`."""

from __future__ import annotations


def register_harness_adapters() -> None:
    from infrastructure.harness_providers import set_investigation_tools_adapter, set_tool_registry
    from tools.investigation.stages.gather_evidence.tools import get_available_tools
    from tools.registry import RegisteredToolRegistry

    set_tool_registry(RegisteredToolRegistry())
    set_investigation_tools_adapter(get_investigation_tools=get_available_tools)
