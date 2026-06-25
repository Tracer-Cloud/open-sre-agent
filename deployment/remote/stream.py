"""SSE stream parser for remote investigation streaming responses.

Supports both ``stream_mode: ["updates"]`` (node-level) and
``stream_mode: ["events"]`` (fine-grained tool/LLM/chain events).

The event envelope itself (:class:`StreamEvent`) lives in
``core.domain.stream`` so the orchestration core can produce
events without importing from this transport-layer module.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from typing import Any

import httpx

from core.domain.stream import StreamEvent


def parse_sse_stream(response: httpx.Response) -> Iterator[StreamEvent]:
    """Parse an SSE byte stream from a ``/runs/stream`` (or compatible) response.

    Expected frame shape::

        event: <type>
        data: <json>
        \\n

    Yields :class:`StreamEvent` for each complete SSE frame.
    """
    current_event_type = ""
    data_lines: list[str] = []

    for line in response.iter_lines():
        if line.startswith("event:"):
            current_event_type = line[len("event:") :].strip()
            data_lines = []
        elif line.startswith("data:"):
            data_lines.append(line[len("data:") :].strip())
        elif line == "":
            if current_event_type and data_lines:
                raw = "\n".join(data_lines)
                yield _build_event(current_event_type, raw)
                current_event_type = ""
                data_lines = []

    if current_event_type and data_lines:
        raw = "\n".join(data_lines)
        yield _build_event(current_event_type, raw)


def _build_event(event_type: str, raw_data: str) -> StreamEvent:
    """Build a StreamEvent from raw SSE fields."""
    try:
        data = json.loads(raw_data) if raw_data else {}
    except json.JSONDecodeError:
        data = {"raw": raw_data}

    node_name = _extract_node_name(event_type, data)
    kind, run_id, tags = _extract_event_details(event_type, data)
    return StreamEvent(
        event_type=event_type,
        data=data,
        node_name=node_name,
        kind=kind,
        run_id=run_id,
        tags=tags,
    )


def _extract_node_name(event_type: str, data: dict[str, Any]) -> str:
    """Extract the pipeline node name from an event payload.

    ``updates`` events have the node name as the sole top-level key.
    ``events`` events carry it in ``metadata.pipeline_node``.
    """
    if event_type == "updates" and isinstance(data, dict):
        keys = [k for k in data if not k.startswith("__")]
        if len(keys) == 1:
            return keys[0]

    if isinstance(data, dict):
        metadata = data.get("metadata", {})
        if isinstance(metadata, dict) and "pipeline_node" in metadata:
            return str(metadata["pipeline_node"])
        if "name" in data:
            return str(data["name"])

    return ""


def _extract_event_details(event_type: str, data: dict[str, Any]) -> tuple[str, str, list[str]]:
    """Extract ``kind``, ``run_id`` and ``tags`` from an events-mode payload.

    Returns ("", "", []) for non-events SSE types.
    """
    if event_type != "events" or not isinstance(data, dict):
        return "", "", []

    kind = str(data.get("event", ""))
    run_id = str(data.get("run_id", ""))
    raw_tags = data.get("tags", [])
    tags: list[str] = list(raw_tags) if isinstance(raw_tags, list) else []
    return kind, run_id, tags


__all__ = ["parse_sse_stream"]
