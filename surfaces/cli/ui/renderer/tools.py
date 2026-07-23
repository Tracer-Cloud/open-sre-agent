"""Tool-call event-payload extraction helpers."""

from __future__ import annotations

from typing import Any


def _tool_event_key(data: dict[str, Any], name: str) -> str:
    nested = data.get("data", {}) if isinstance(data.get("data"), dict) else {}
    return str(
        data.get("id")
        or data.get("tool_call_id")
        or nested.get("id")
        or nested.get("tool_call_id")
        or name
    )


def _tool_input(data: dict[str, Any]) -> Any:
    nested = data.get("data", {}) if isinstance(data.get("data"), dict) else {}
    return data.get("input", nested.get("input", {}))


def _tool_output(data: dict[str, Any]) -> Any:
    nested = data.get("data", {}) if isinstance(data.get("data"), dict) else {}
    return data.get("output", nested.get("output", {}))
