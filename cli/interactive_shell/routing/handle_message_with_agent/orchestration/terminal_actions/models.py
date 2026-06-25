"""Shared data models for terminal action planning and execution."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from cli.interactive_shell.routing.handle_message_with_agent.orchestration.interaction_models import (
    PlannedAction,
)


@dataclass(frozen=True)
class TerminalActionExecutionResult:
    planned_count: int
    executed_count: int
    executed_success_count: int
    has_unhandled_clause: bool
    handled: bool
    response_text: str = ""


@dataclass(frozen=True)
class ActionExecutionDeps:
    """Optional dependency seams used by tests/harnesses."""

    planner: Callable[..., Any] | None = None
    dispatch: Callable[..., bool] | None = None


@dataclass(frozen=True)
class ActionPlanningDecision:
    # v0.1: there is no planning-stage fail-closed denial. All terminal actions
    # are read-only, so an unmatched/ambiguous clause never blocks the turn — it
    # falls through to the matched actions or to the conversational assistant.
    # ``denied`` was removed for this reason (see cli/interactive_shell/AGENTS.md).
    actions: tuple[PlannedAction, ...]
    has_unhandled_clause: bool
    policy_trace: tuple[str, ...]


__all__ = [
    "ActionExecutionDeps",
    "ActionPlanningDecision",
    "TerminalActionExecutionResult",
]
