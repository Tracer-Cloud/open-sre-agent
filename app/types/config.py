"""Project-owned node execution config types."""

from __future__ import annotations

from typing import Any, TypedDict
from uuid import UUID

Configurable = dict[str, Any]


class NodeConfig(TypedDict, total=False):
    """Config shape consumed by OpenSRE nodes.

    LangGraph may pass a richer runtime config, but core nodes only depend on
    these project-owned fields. Top-level ``run_id`` matches LangChain
    ``RunnableConfig.run_id`` (``UUID | None``); ``configurable`` often carries a
    string run id from callers.
    """

    configurable: Configurable
    metadata: dict[str, Any]
    tags: list[str]
    run_name: str
    run_id: UUID | str | None


def get_configurable(config: NodeConfig | None) -> Configurable:
    """Return the configurable payload from a node config."""
    if not config:
        return {}
    configurable = config.get("configurable", {})
    return configurable if isinstance(configurable, dict) else {}
