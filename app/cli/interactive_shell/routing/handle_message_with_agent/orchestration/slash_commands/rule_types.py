"""Typed contracts for deterministic routing rules."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field

from app.cli.interactive_shell.routing.handle_message_with_agent.orchestration.interaction_models import (
    PlannedAction,
    PromptClause,
)


@dataclass(frozen=True)
class MatchWindowStrategy:
    """Named strategy for extracting bounded context around a term match."""

    name: str
    left_chars: int
    right_chars: int

    def slice(self, text: str, *, anchor_start: int) -> str:
        start = max(0, anchor_start - self.left_chars)
        end = min(len(text), anchor_start + self.right_chars)
        return text[start:end]


@dataclass
class ClauseRuleContext:
    """Mutable context shared across ordered clause-rule execution."""

    clause: PromptClause
    normalized_text: str
    seen_slash: set[str]
    mapped: list[PlannedAction] = field(default_factory=list)
    trace: list[str] = field(default_factory=list)
    matched_slash_registry: bool = False


@dataclass(frozen=True)
class ClauseRuleSpec:
    """Declarative rule entry used by the mapper precedence table."""

    name: str
    evaluator: Callable[[ClauseRuleContext], bool]


INTEGRATION_WINDOW = MatchWindowStrategy(name="integration_window", left_chars=80, right_chars=120)
INTEGRATION_DETAIL_WINDOW = MatchWindowStrategy(
    name="integration_detail_window", left_chars=30, right_chars=70
)
