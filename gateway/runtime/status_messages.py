"""User-facing status copy for the Telegram gateway placeholder message.

Every status funnels through :func:`normalize_gateway_status`, which swaps the
legacy ``Working…`` placeholder (and empty text) for real copy.
"""

from __future__ import annotations

import random
import re
from functools import lru_cache
from typing import Any

INITIAL_STATUSES: tuple[str, ...] = (
    "🔍 On it — give me a moment…",
    "⚡ Digging in…",
    "🛠️ Checking your stack…",
    "📡 Pulling the latest signals…",
)

_WORKING_PLACEHOLDER = re.compile(r"working(\.{3}|…)?", re.IGNORECASE)


def initial_status_message() -> str:
    """Return a short, varied placeholder shown while the first turn starts."""
    return random.choice(INITIAL_STATUSES)


def normalize_gateway_status(text: str) -> str:
    """Swap empty or legacy ``Working…`` copy for real status text."""
    stripped = text.strip()
    if not stripped or _WORKING_PLACEHOLDER.fullmatch(stripped):
        return initial_status_message()
    return text


def status_from_response_label(label: str) -> str:
    """Map harness response labels (e.g. ``assistant``) to Telegram status text."""
    label = label.strip()
    if not label or _WORKING_PLACEHOLDER.fullmatch(label):
        return initial_status_message()
    if label.lower() == "assistant":
        return "💬 Composing your reply…"
    return f"✨ {label}…"


def status_from_tool_start(tool_name: str, tool_input: Any = None) -> str:
    """Build a one-line ``⏳ label… (hint)`` status while an action tool runs."""
    name = tool_name.strip()
    if not name:
        return initial_status_message()
    return f"⏳ {_tool_label(name)}…{_input_hint(tool_input)}"


@lru_cache(maxsize=256)
def _tool_label(tool_name: str) -> str:
    """First clause of the tool's display name or description, else its humanized name."""
    from tools.registry import get_registered_tools

    tool = next((t for t in get_registered_tools() if t.name == tool_name), None)
    candidates = (tool.display_name or "", tool.description) if tool else ()
    for text in (*candidates, tool_name.replace("_", " ")):
        clause = re.split(r"\.\s| — | - |; ", " ".join(text.split()), maxsplit=1)[0]
        if len(clause) > 72:
            clause = f"{clause[:71]}…"
        if clause := clause.rstrip("."):
            return clause
    return tool_name


def _input_hint(tool_input: Any) -> str:
    """First meaningful argument value, shortened, as an inline ``(hint)``."""
    if not isinstance(tool_input, dict):
        return ""
    for value in tool_input.values():
        items = value if isinstance(value, list) else [value] if isinstance(value, str) else []
        text = " ".join(part for item in items if (part := " ".join(str(item).split())))
        if text:
            return f" ({text[:45]}…)" if len(text) > 48 else f" ({text})"
    return ""


__all__ = [
    "initial_status_message",
    "normalize_gateway_status",
    "status_from_response_label",
    "status_from_tool_start",
]
