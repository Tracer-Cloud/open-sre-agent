"""Deterministic command detection used by the agent's pre-LLM fast path."""

from __future__ import annotations

from cli.interactive_shell.routing.handle_message_with_agent.command_dispatch.catalog import (
    BARE_COMMAND_ALIAS_MAP,
    BARE_COMMAND_ALIASES,
    BARE_COMMAND_ALIASES_WITH_ARGS,
)
from cli.interactive_shell.routing.handle_message_with_agent.command_dispatch.detection import (
    deterministic_command_text,
    is_bare_command_alias,
    opensre_investigate_slash_text,
    slash_dispatch_text,
)

__all__ = [
    "BARE_COMMAND_ALIASES",
    "BARE_COMMAND_ALIASES_WITH_ARGS",
    "BARE_COMMAND_ALIAS_MAP",
    "deterministic_command_text",
    "is_bare_command_alias",
    "opensre_investigate_slash_text",
    "slash_dispatch_text",
]
