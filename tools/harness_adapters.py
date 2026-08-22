"""Wire tools-layer helpers into :mod:`infrastructure.harness_providers`."""

from __future__ import annotations


def register_harness_adapters() -> None:
    from infrastructure.harness_providers import ToolSources
    from tools.investigation.stages.gather_evidence.tools import get_available_tools
    from tools.registry import RegisteredToolRegistry

    ToolSources(
        registry=RegisteredToolRegistry(),
        investigation_tools=get_available_tools,
    ).install()
