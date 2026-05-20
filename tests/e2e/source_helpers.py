"""Helpers for E2E tests that exercise the current tool-registry architecture."""

from __future__ import annotations

from typing import Any

from app.agent.investigation import _availability_view
from app.tools.registry import get_registered_tools


def resolve_available_tool_sources(
    resolved_integrations: dict[str, dict[str, Any]],
) -> dict[str, dict[str, Any]]:
    """Return integration configs that expose at least one investigation tool.

    The current runtime derives source availability directly from registered tools and the
    resolved integrations map (older pipelines used a dedicated source-detection stage).
    """
    sources: dict[str, dict[str, Any]] = {}
    tools = get_registered_tools("investigation")
    availability_sources = _availability_view(resolved_integrations)

    for source_name, config in availability_sources.items():
        if source_name == "_all" or not isinstance(config, dict):
            continue

        source_tools = [tool for tool in tools if tool.source == source_name]
        if not source_tools:
            sources[source_name] = dict(config)
            continue

        available_tools = [tool for tool in source_tools if tool.is_available(availability_sources)]
        if not available_tools:
            continue

        source_config = dict(config)
        for tool in available_tools:
            source_config.update(tool.extract_params(availability_sources))
        sources[source_name] = source_config

    return sources
