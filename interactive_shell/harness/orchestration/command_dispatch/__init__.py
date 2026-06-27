"""Literal command detection used by terminal UI policy."""

from __future__ import annotations

from interactive_shell.harness.orchestration.command_dispatch.catalog import (
    BARE_COMMAND_ALIAS_MAP,
    BARE_COMMAND_ALIASES,
    BARE_COMMAND_ALIASES_WITH_ARGS,
)
from interactive_shell.harness.orchestration.command_dispatch.detection import (
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
