"""Bare-alias matching and slash-dispatch normalization helpers."""

from __future__ import annotations

import re

from app.cli.interactive_shell.routing.handle_message_with_agent.orchestration.intent_parser import (
    is_single_edit_typo,
    normalize_intent_text,
)
from app.cli.interactive_shell.routing.resolve_cli_command.catalog import (
    BARE_COMMAND_ALIAS_MAP,
    BARE_COMMAND_ALIASES,
    BARE_COMMAND_ALIASES_WITH_ARGS,
)

_OPENSRE_WRAPPED_SLASH_RE = re.compile(r"^/opensre(?:\s+(?P<inner>.+))?$", re.IGNORECASE)
_OPENSRE_INVESTIGATE_RE = re.compile(
    r"^\s*opensre\s+investigate(?:\s+(?:-i|--input|--input-file)\s+(?P<path>\S+))?\s*$",
    re.IGNORECASE,
)


def _unwrap_opensre_wrapped_slash(text: str) -> str:
    match = _OPENSRE_WRAPPED_SLASH_RE.match(text)
    if match is None:
        return text
    inner = (match.group("inner") or "").strip()
    if not inner:
        return text
    if inner.startswith("/"):
        return inner
    return f"/{inner}"


def opensre_investigate_slash_text(text: str) -> str | None:
    """Map ``opensre investigate -i <file>`` to ``/investigate <file>`` for deterministic routing."""
    match = _OPENSRE_INVESTIGATE_RE.match(text.strip())
    if match is None:
        return None
    alert_path = match.group("path") or "alert.json"
    return f"/investigate {alert_path}"


def is_bare_command_alias(text: str) -> bool:
    """True when ``text`` is a bare slash-command alias or accepted typo."""
    stripped = text.strip()
    if stripped.lower() in BARE_COMMAND_ALIASES:
        return True
    first, sep, _rest = stripped.partition(" ")
    if sep and first.lower() in BARE_COMMAND_ALIASES_WITH_ARGS:
        return True
    normalized = normalize_intent_text(stripped)
    if normalized not in BARE_COMMAND_ALIASES:
        return False
    return is_single_edit_typo(stripped.lower(), normalized)


def slash_dispatch_text(text: str) -> str:
    """Return slash command text, including typo-tolerant bare alias mapping."""
    stripped = text.strip()
    if stripped.startswith("/"):
        return _unwrap_opensre_wrapped_slash(stripped)
    first, sep, rest = stripped.partition(" ")
    if sep:
        mapped_first = BARE_COMMAND_ALIAS_MAP.get(first.lower())
        if mapped_first is not None and first.lower() in BARE_COMMAND_ALIASES_WITH_ARGS:
            return f"{mapped_first} {rest.strip()}"
    normalized = normalize_intent_text(stripped)
    mapped = BARE_COMMAND_ALIAS_MAP.get(normalized)
    if mapped is not None:
        return mapped
    return f"/{stripped}"


__all__ = [
    "is_bare_command_alias",
    "opensre_investigate_slash_text",
    "slash_dispatch_text",
]
