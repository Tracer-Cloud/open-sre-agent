"""Bare-alias matching and slash-dispatch normalization helpers."""

from __future__ import annotations

from app.cli.interactive_shell.intent.intent_parser import (
    is_single_edit_typo,
    normalize_intent_text,
)
from app.cli.interactive_shell.routing.resolve_cli_command.catalog import (
    BARE_COMMAND_ALIAS_MAP,
    BARE_COMMAND_ALIASES,
    BARE_COMMAND_ALIASES_WITH_ARGS,
)


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
        return stripped
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
    "slash_dispatch_text",
]
