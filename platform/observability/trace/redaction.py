"""Helpers for safe tool-call tracing in CLI and reports."""

from __future__ import annotations

import json
import re
from typing import Any

_SENSITIVE_KEY_RE = re.compile(
    r"(api[_-]?key|token|secret|password|credential|authorization|auth[_-]?header)",
    re.IGNORECASE,
)
_RUNTIME_KEY_RE = re.compile(r"(^_|backend$|_backend$)", re.IGNORECASE)


def redact_sensitive(value: Any) -> Any:
    """Return a copy of ``value`` with credentials and runtime objects hidden."""
    if isinstance(value, dict):
        redacted: dict[str, Any] = {}
        for key, item in value.items():
            key_str = str(key)
            if _SENSITIVE_KEY_RE.search(key_str):
                redacted[key_str] = "[redacted]"
            elif _RUNTIME_KEY_RE.search(key_str):
                redacted[key_str] = "[runtime object]"
            else:
                redacted[key_str] = redact_sensitive(item)
        return redacted
    if isinstance(value, list):
        return [redact_sensitive(item) for item in value]
    if isinstance(value, tuple):
        return [redact_sensitive(item) for item in value]
    return value


def format_json_preview(value: Any, *, max_chars: int = 4000) -> str:
    """Pretty-print a redacted JSON-ish value, bounded for terminal output."""
    redacted = redact_sensitive(value)
    try:
        text = json.dumps(redacted, indent=2, default=str)
    except TypeError:
        text = str(redacted)
    if len(text) <= max_chars:
        return text
    return text[: max_chars - 24].rstrip() + "\n... [truncated]"


def format_tool_trace_entry(entry: dict[str, Any], *, max_output_chars: int = 1200) -> str:
    """Format one evidence entry as a compact report line."""
    tool_name = str(entry.get("tool_name") or entry.get("key") or "tool")
    loop = entry.get("loop_iteration")
    loop_label = "seed" if loop == -1 else f"iteration {loop}"
    args = format_json_preview(entry.get("tool_args") or {}, max_chars=500)
    output = format_json_preview(entry.get("data"), max_chars=max_output_chars)
    return f"- `{tool_name}` ({loop_label})\n  input: `{_one_line(args)}`\n  output: `{_one_line(output)}`"


def _one_line(value: str) -> str:
    return " ".join(value.split())
