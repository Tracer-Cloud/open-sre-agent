"""Slash command handlers for the REPL (compatibility shim for the modular registry)."""

from __future__ import annotations

from app.cli.interactive_shell.command_registry import (
    SLASH_COMMANDS,
    dispatch_slash,
    switch_llm_provider,
    switch_toolcall_model,
)
from app.cli.interactive_shell.command_registry.types import SlashCommand

__all__ = [
    "SLASH_COMMANDS",
    "SlashCommand",
    "dispatch_slash",
    "switch_llm_provider",
    "switch_toolcall_model",
]
