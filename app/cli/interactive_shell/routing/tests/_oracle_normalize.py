"""Normalization helpers for routing action oracle tests."""

from __future__ import annotations

import re
from typing import Any

from app.cli.interactive_shell.routing.handle_message_with_agent.orchestration.interaction_models import (
    PlannedAction,
)

_ANSI_ESCAPE_RE = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")
_WHITESPACE_RE = re.compile(r"\s+")


def normalize_response_text(text: str) -> str:
    """Normalize terminal output for stable substring assertions."""
    without_ansi = _ANSI_ESCAPE_RE.sub("", text)
    collapsed = _WHITESPACE_RE.sub(" ", without_ansi).strip()
    return collapsed.casefold()


def normalize_planned_action(action: PlannedAction) -> dict[str, Any]:
    """Convert a PlannedAction into a comparable oracle structure."""
    normalized: dict[str, Any] = {
        "kind": action.kind,
        "source": action.source,
        "target_surface": action.target_surface or "",
    }
    content = action.content.strip()

    if action.kind == "slash":
        parts = content.split()
        normalized["command"] = parts[0] if parts else ""
        normalized["args"] = parts[1:] if len(parts) > 1 else []
    elif action.kind == "synthetic_test":
        suite, _sep, scenario = content.partition(":")
        normalized["suite"] = suite
        normalized["scenario"] = scenario
    elif action.kind == "cli_command":
        normalized["payload"] = content
    elif action.kind == "sample_alert":
        normalized["template"] = content
    else:
        normalized["content"] = content
    return normalized


def normalize_history_entry(entry: dict[str, Any]) -> dict[str, Any]:
    """Normalize session history row text while preserving type + status."""
    text = str(entry.get("text", ""))
    text_normalized = _WHITESPACE_RE.sub(" ", text).strip().casefold()
    return {
        "type": str(entry.get("type", "")),
        "text_normalized": text_normalized,
        "ok": bool(entry.get("ok", True)),
    }


def cli_command_payload_matches(actual: str, expected: str) -> bool:
    """True when *actual* matches *expected* or echoes it with a user prefix."""
    actual_n = actual.strip().casefold()
    expected_n = expected.strip().casefold()
    if not actual_n or not expected_n:
        return False
    if actual_n == expected_n:
        return True
    # Non-canonical user prefixes (e.g. sniffdog) may appear before the subcommand.
    return actual_n.endswith(expected_n) or expected_n in actual_n


def oracle_action_matches(actual_norm: dict[str, Any], expected: dict[str, Any]) -> bool:
    """Compare only expected keys to keep fixture declarations compact."""
    for key, expected_value in expected.items():
        if key == "payload" and str(actual_norm.get("kind", "")) == "cli_command":
            if not cli_command_payload_matches(
                str(actual_norm.get(key, "")),
                str(expected_value),
            ):
                return False
            continue
        if actual_norm.get(key) != expected_value:
            return False
    return True


__all__ = [
    "cli_command_payload_matches",
    "normalize_history_entry",
    "normalize_planned_action",
    "normalize_response_text",
    "oracle_action_matches",
]
