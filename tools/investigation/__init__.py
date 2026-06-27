"""Composite investigation capability and agent-callable tool."""

from __future__ import annotations

from typing import Any

from tools.investigation.capability import (
    astream_investigation,
    build_investigation_payload,
    resolve_investigation_context,
    run_investigation,
    run_investigation_payload,
)
from tools.tool_decorator import tool


@tool(
    name="run_investigation",
    display_name="Run investigation",
    source="knowledge",
    description=(
        "Run the full OpenSRE investigation workflow for an alert or incident description."
    ),
    side_effect_level="external",
    surfaces=("chat",),
    tags=("investigation", "composite"),
    cost_tier="expensive",
    input_schema={
        "type": "object",
        "properties": {
            "raw_alert": {
                "type": "string",
                "description": "Alert JSON or a concrete incident description to investigate.",
            },
            "opensre_evaluate": {
                "type": "boolean",
                "default": False,
                "description": "Whether to run optional OpenSRE rubric evaluation.",
            },
        },
        "required": ["raw_alert"],
    },
)
def run_investigation_tool(
    raw_alert: str,
    opensre_evaluate: bool = False,
) -> dict[str, Any]:
    """Agent-callable wrapper around the product investigation workflow."""
    return run_investigation_payload(
        raw_alert=raw_alert,
        opensre_evaluate=opensre_evaluate,
    )


__all__ = [
    "astream_investigation",
    "build_investigation_payload",
    "resolve_investigation_context",
    "run_investigation",
    "run_investigation_payload",
    "run_investigation_tool",
]
