"""Bare slash-command alias detection and normalization helpers."""

from __future__ import annotations

from app.cli.interactive_shell.routing.resolve_cli_command.catalog import (
    BARE_COMMAND_ALIAS_MAP,
    BARE_COMMAND_ALIASES,
    BARE_COMMAND_ALIASES_BY_TARGET,
    BARE_COMMAND_ALIASES_WITH_ARGS,
)
from app.cli.interactive_shell.routing.resolve_cli_command.matcher import (
    is_bare_command_alias,
    slash_dispatch_text,
)

__all__ = [
    "BARE_COMMAND_ALIASES",
    "BARE_COMMAND_ALIASES_BY_TARGET",
    "BARE_COMMAND_ALIASES_WITH_ARGS",
    "BARE_COMMAND_ALIAS_MAP",
    "is_bare_command_alias",
    "slash_dispatch_text",
]
