"""Slash-command type definitions."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from rich.console import Console

from app.cli.interactive_shell.execution_tier import ExecutionTier
from app.cli.interactive_shell.session import ReplSession


@dataclass(frozen=True)
class SlashCommand:
    name: str
    help_text: str
    handler: Callable[[ReplSession, Console, list[str]], bool]
    #: Tab-completion hints for the first argument after the command name (keyword, meta text).
    first_arg_completions: tuple[tuple[str, str], ...] = ()
    execution_tier: ExecutionTier = ExecutionTier.SAFE


__all__ = ["ExecutionTier", "SlashCommand"]
