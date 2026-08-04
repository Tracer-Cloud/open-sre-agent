"""Canonical provider IDs and discovery-name to provider-ID mapping.

The provider-ID set is the shared contract every layer of the agents
stack agrees on: discovery classifies commands into these names,
the token source and meter registries key off the same names, and
``provider_for`` resolves an ``AgentRecord`` to one of them. Keeping
the constants and the small name-resolution helper here lets all of
those consumers depend on a single thin module instead of pulling in
the heavier discovery (``ps``, process dedupe, Cursor terminal
metadata) just to read the enum.

``FleetAgentProvider`` is a closed, product-catalog vocabulary (fleet
discovery/meters agree on this set of names).
"""

from __future__ import annotations

from enum import StrEnum


class FleetAgentProvider(StrEnum):
    """Coding-agent processes the fleet monitor can identify and meter."""

    CLAUDE_CODE = "claude-code"
    CODEX = "codex"
    CURSOR = "cursor"
    AIDER = "aider"
    GEMINI_CLI = "gemini-cli"
    ANTIGRAVITY_CLI = "antigravity-cli"
    OPENCODE = "opencode"
    KIMI = "kimi"
    COPILOT = "copilot"


# ``cursor-claude-code`` is the Anthropic extension wrapping the real
# ``claude`` binary with ``--output-format stream-json``; same NDJSON,
# same meter. The other cursor flavors emit plain text.
_CURSOR_FAMILY_TO_PROVIDER: dict[str, FleetAgentProvider] = {
    "cursor-claude-code": FleetAgentProvider.CLAUDE_CODE,
    "cursor-agent-exec": FleetAgentProvider.CURSOR,
    "cursor-agent": FleetAgentProvider.CURSOR,
}


def provider_from_classified_name(name: str) -> FleetAgentProvider | None:
    """Derive a canonical provider id from a discovery-style name."""
    base = _strip_pid_suffix(name)
    if base in _CURSOR_FAMILY_TO_PROVIDER:
        return _CURSOR_FAMILY_TO_PROVIDER[base]
    if base in FleetAgentProvider:
        return FleetAgentProvider(base)
    return None


def _strip_pid_suffix(name: str) -> str:
    if "-" not in name:
        return name
    base, _, tail = name.rpartition("-")
    if tail.isdigit():
        return base
    return name


__all__ = ["FleetAgentProvider", "provider_from_classified_name"]
