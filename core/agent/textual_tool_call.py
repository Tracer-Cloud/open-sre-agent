"""Detect a tool invocation that the model emitted as plain text.

Providers occasionally degrade a tool call into the assistant's text content:
instead of a structured ``tool_calls`` entry, the reply body is a bare JSON
object holding the arguments (``{"command": "/cron", "args": ["list"]}``) or a
``{"name": ..., "arguments": ...}`` envelope. To the ReAct loop that reads as
"no tool calls", so the JSON blob would be accepted as the final answer and the
turn ends with the requested action never executed. The loop uses this
detector to recognize the degraded shape and nudge the model to re-emit a real
tool call instead of concluding.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from typing import Any

TEXTUAL_TOOL_CALL_NUDGE = (
    "Your last reply wrote a tool invocation as plain text, so nothing was "
    "executed. Emit it as a real tool call through the tool-calling interface. "
    "If you are instead done with the task, reply in prose without any JSON "
    "tool payload."
)

_CODE_FENCE = "```"


def looks_like_textual_tool_call(content: str, tools: Sequence[Any]) -> bool:
    """Return True when ``content`` is one or more JSON tool invocations as text.

    A match means the whole reply (modulo a markdown code fence) parses as a
    JSON object, a JSON array of objects, or newline-separated JSON objects,
    where EVERY object fits one plausible reading: either the argument schema
    of one of ``tools`` (all keys known, all required keys present) or a
    ``{"name": <tool>, "arguments": {...}}`` envelope naming one of them. The
    batched shapes matter: a model chaining per-id calls (one remove per listed
    task) degrades into an array or one object per line, not a single object.
    Prose replies that merely contain JSON somewhere do not match.
    """
    payloads = _candidate_payloads(content)
    if not payloads:
        return False
    return all(
        _is_named_call_envelope(payload, tools)
        or any(_matches_tool_arguments(payload, tool) for tool in tools)
        for payload in payloads
    )


def _candidate_payloads(content: str) -> list[dict[str, Any]]:
    text = content.strip()
    if text.startswith(_CODE_FENCE) and text.endswith(_CODE_FENCE):
        inner = text[len(_CODE_FENCE) : -len(_CODE_FENCE)]
        # Drop an optional language tag on the opening fence line.
        text = inner.split("\n", 1)[1].strip() if "\n" in inner else inner.strip()
    single = _parse_json_container(text)
    if single is not None:
        return single
    # One JSON object per line (a degraded multi-call batch).
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    if len(lines) < 2:
        return []
    per_line: list[dict[str, Any]] = []
    for line in lines:
        parsed = _parse_json_container(line)
        if parsed is None or len(parsed) != 1:
            return []
        per_line.extend(parsed)
    return per_line


def _parse_json_container(text: str) -> list[dict[str, Any]] | None:
    """Parse one JSON object or array of objects; None when ``text`` is neither."""
    is_object = text.startswith("{") and text.endswith("}")
    is_array = text.startswith("[") and text.endswith("]")
    if not is_object and not is_array:
        return None
    try:
        parsed = json.loads(text)
    except ValueError:
        return None
    items = [parsed] if isinstance(parsed, dict) else parsed if isinstance(parsed, list) else None
    if not items or not all(isinstance(item, dict) and item for item in items):
        return None
    return items


def _is_named_call_envelope(payload: dict[str, Any], tools: Sequence[Any]) -> bool:
    name = payload.get("name")
    if not isinstance(name, str):
        return False
    if not set(payload) <= {"name", "arguments", "input", "args", "id"}:
        return False
    return any(getattr(tool, "name", None) == name for tool in tools)


def _matches_tool_arguments(payload: dict[str, Any], tool: Any) -> bool:
    schema = getattr(tool, "input_schema", None)
    if not isinstance(schema, dict):
        return False
    properties = schema.get("properties")
    if not isinstance(properties, dict) or not properties:
        return False
    required = schema.get("required") or []
    keys = set(payload)
    return keys <= set(properties) and set(required) <= keys


__all__ = ["TEXTUAL_TOOL_CALL_NUDGE", "looks_like_textual_tool_call"]
