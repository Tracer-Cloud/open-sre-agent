"""Slash command /model package exports."""

from __future__ import annotations

from cli.interactive_shell.command_registry.model.command import COMMANDS, parse_model_set_args
from cli.interactive_shell.command_registry.model.switching import (
    restore_default_model,
    switch_llm_provider,
    switch_reasoning_model,
    switch_toolcall_model,
)

__all__ = [
    "COMMANDS",
    "parse_model_set_args",
    "restore_default_model",
    "switch_llm_provider",
    "switch_reasoning_model",
    "switch_toolcall_model",
]
