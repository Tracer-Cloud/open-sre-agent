"""Tracer run tasks tool."""

from __future__ import annotations

from dataclasses import asdict
from typing import Any

from core.domain.types.evidence import record_evidence_entry
from core.domain.types.tools import ToolSurface
from core.tool_framework import tool
from integrations.tracer import get_tracer_client


def _map_get_tracer_tasks(
    evidence: dict[str, Any], output: dict[str, Any], _tool_input: dict[str, Any]
) -> None:
    """Cite the task count and how many failed for a pipeline run."""
    if not output.get("found"):
        return
    total = output.get("total_tasks", 0)
    if not total:
        return
    failed = output.get("failed_tasks", 0)
    completed = output.get("completed_tasks", 0)
    parts = [f"{total} task(s)"]
    if failed:
        parts.append(f"{failed} failed")
    if completed:
        parts.append(f"{completed} completed")
    record_evidence_entry(
        evidence,
        source="get_tracer_tasks",
        label="Tracer Run Tasks",
        summary=", ".join(parts),
    )


@tool(
    name="get_tracer_tasks",
    source="tracer_web",
    description="Get tasks for a specific pipeline run from the Tracer API.",
    use_cases=[
        "Retrieving detailed task information for a pipeline run",
        "Understanding which specific tasks failed or succeeded",
    ],
    requires=["run_id"],
    input_schema={
        "type": "object",
        "properties": {
            "run_id": {
                "type": "string",
                "description": "The unique identifier for the pipeline run",
            },
        },
        "required": ["run_id"],
    },
    is_available=lambda sources: bool(sources.get("tracer_web")),
    surfaces=(ToolSurface.INVESTIGATION, ToolSurface.CHAT),
    evidence_mapper=_map_get_tracer_tasks,
)
def get_tracer_tasks(run_id: str) -> dict[str, Any]:
    """Get tasks for a specific pipeline run from the Tracer API."""
    client = get_tracer_client()
    return asdict(client.get_run_tasks(run_id))
