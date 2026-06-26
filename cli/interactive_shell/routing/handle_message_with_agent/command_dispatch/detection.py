"""Deterministic command detection for the interactive-shell agent fast path.

These helpers decide whether a turn is a literal slash command, a bare command
alias (including single-edit typos), or an ``opensre investigate`` quick-start,
and return the normalized slash command text to dispatch. They never call the
LLM; the agent uses them to short-circuit straight to slash dispatch.

==============================================================================
HARD RULE (NON-NEGOTIABLE): THIS LAYER ONLY RECOGNIZES LITERAL CLI COMMANDS.
==============================================================================
This fast path exists for ONE purpose: dispatching explicit, literal CLI/slash
commands and their bare command aliases. That is ALL it may ever do.

DO NOT add ANYTHING that infers user *intent* from natural language. NEVER EVER
add regex (or keyword/substring/fuzzy/NLU matching) that maps free-form text to
an action — e.g. detecting "investigate a sample alert", "show my integrations",
or any phrasing-based behavior. All intent decisions belong to the LLM action
planner, NOT here. Intent regex in this layer is exactly the bug class that sent
sample-alert requests to the wrong place; it will not be reintroduced.

If you add anything to this layer other than literal CLI command detection,
YOU WILL BE FIRED. No exceptions. When in doubt, return ``None`` and let the
LLM planner decide.
"""

from __future__ import annotations

import re
import shlex

from cli.interactive_shell.routing.handle_message_with_agent.command_dispatch.catalog import (
    BARE_COMMAND_ALIAS_MAP,
    BARE_COMMAND_ALIASES,
    BARE_COMMAND_ALIASES_WITH_ARGS,
)
from cli.interactive_shell.routing.handle_message_with_agent.orchestration.intent_parser import (
    is_single_edit_typo,
    normalize_intent_text,
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
    """Map ``opensre investigate -i <file>`` to ``/investigate <file>`` for deterministic dispatch."""
    stripped = text.strip()
    match = _OPENSRE_INVESTIGATE_RE.match(stripped)
    if match is not None:
        alert_path = match.group("path") or "alert.json"
        return f"/investigate {alert_path}"

    try:
        tokens = shlex.split(stripped)
    except ValueError:
        return None
    if len(tokens) < 2 or tokens[0].lower() != "opensre" or tokens[1].lower() != "investigate":
        return None
    if len(tokens) == 2:
        return "/investigate alert.json"
    if len(tokens) == 4 and tokens[2].lower() in {"-i", "--input", "--input-file"}:
        return f"/investigate {tokens[3]}"
    if len(tokens) == 3 and tokens[2].lower().startswith("--input-file="):
        alert_path = tokens[2].split("=", 1)[1].strip()
        if alert_path:
            return f"/investigate {alert_path}"
    return None


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


def deterministic_command_text(text: str) -> str | None:
    """Return normalized slash command text for deterministic command input, else ``None``.

    Handles ``opensre investigate`` quick-start, literal ``/slash`` input, and
    bare command aliases (including single-edit typos). Returns ``None`` for any
    input that should fall through to the LLM-backed agent.
    """
    investigate_slash = opensre_investigate_slash_text(text)
    if investigate_slash is not None:
        return investigate_slash
    stripped = text.strip()
    if stripped.startswith("/") or is_bare_command_alias(text):
        return slash_dispatch_text(text)
    return None


__all__ = [
    "deterministic_command_text",
    "is_bare_command_alias",
    "opensre_investigate_slash_text",
    "slash_dispatch_text",
]
