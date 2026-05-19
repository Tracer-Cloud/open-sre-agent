"""Deterministic resolver for slash and bare-alias command input."""

from __future__ import annotations

from app.cli.interactive_shell.routing.resolve_cli_command.evaluator import resolve_cli_command
from app.cli.interactive_shell.routing.resolve_cli_command.matcher import (
    is_bare_command_alias,
    slash_dispatch_text,
)

__all__ = [
    "is_bare_command_alias",
    "resolve_cli_command",
    "slash_dispatch_text",
]
