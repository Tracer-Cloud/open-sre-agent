"""Slash-command type definitions."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from rich.console import Console

from app.cli.interactive_shell.routing.handle_message_with_agent.orchestration.execution_tier import (
    ExecutionTier,
)
from app.cli.interactive_shell.runtime import ReplSession


@dataclass(frozen=True)
class SlashCommand:
    name: str
    description: str
    handler: Callable[[ReplSession, Console, list[str]], bool]
    usage: tuple[str, ...] = ()
    examples: tuple[str, ...] = ()
    notes: tuple[str, ...] = ()
    #: Tab-completion hints for the first argument after the command name (keyword, meta text).
    first_arg_completions: tuple[tuple[str, str], ...] = ()
    execution_tier: ExecutionTier = ExecutionTier.SAFE
    #: Optional pre-policy arg validator. Returns ``None`` if args are valid, or
    #: a user-facing error string (rendered via ``console.print``) to short-circuit
    #: dispatch with no policy prompt and no handler invocation.
    validate_args: Callable[[list[str]], str | None] | None = None
    #: Multi-sentence description for LLM planners; falls back to ``description``.
    llm_description: str = ""
    #: Natural-language triggers that should route to this command.
    use_cases: tuple[str, ...] = ()
    #: Requests that look similar but should NOT use this command.
    anti_examples: tuple[str, ...] = ()
    #: JSON Schema for positional args after the command name (optional override).
    args_schema: dict[str, Any] | None = None


__all__ = ["ExecutionTier", "SlashCommand"]
