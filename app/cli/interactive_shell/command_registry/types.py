"""Slash-command type definitions."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from rich.console import Console

from app.cli.interactive_shell.orchestration.execution_tier import ExecutionTier
from app.cli.interactive_shell.runtime import ReplSession


@dataclass(frozen=True)
class SlashCommand:
    name: str
    help_text: str
    handler: Callable[[ReplSession, Console, list[str]], bool]
    #: Tab-completion hints for the first argument after the command name (keyword, meta text).
    first_arg_completions: tuple[tuple[str, str], ...] = ()
    execution_tier: ExecutionTier = ExecutionTier.SAFE
    #: Optional pre-policy arg validator. Returns ``None`` if args are valid, or
    #: a user-facing error string (rendered via ``console.print``) to short-circuit
    #: dispatch with no policy prompt and no handler invocation.
    validate_args: Callable[[list[str]], str | None] | None = None


__all__ = ["ExecutionTier", "SlashCommand"]
