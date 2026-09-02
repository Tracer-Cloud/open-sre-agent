"""Wire tools-layer helpers into :mod:`infrastructure.harness_providers`."""

from __future__ import annotations


def register_harness_adapters() -> None:
    from infrastructure.harness_providers import ToolSources
    from tools.registry import RegisteredToolRegistry

    ToolSources(registry=RegisteredToolRegistry()).install()
