"""Agent-callable tools for Compute Cloud."""

from __future__ import annotations

from integrations.yc_compute.tools.yc_instances_tool import (
    get_yc_instance_diagnostics,
    list_yc_instances,
)

__all__ = ["get_yc_instance_diagnostics", "list_yc_instances"]
