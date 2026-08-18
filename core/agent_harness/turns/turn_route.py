"""Pure turn-path routing — no I/O, no stream bookkeeping.

``run_turn`` asks :func:`route_turn` *what* to do, then executes that path.
Post-answer CTA / Want-me-to / stream flush live in
:mod:`core.agent_harness.turns.answer_finalize` and must never gate this
decision (regression: ``text_changed_after_streaming`` UnboundLocalError).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from core.agent_harness.turns.turn_results import ToolCallingTurnResult

RouteIntent = Literal["summarize_observation", "handled_without_llm", "gather_and_answer"]


@dataclass(frozen=True)
class TurnRoutingInput:
    """Minimal facts the turn router decides on, snapshotted from the world."""

    action_handled: bool
    executed_success_count: int
    has_observation: bool
    investigation_dispatched: bool = False


@dataclass(frozen=True)
class TurnRoute:
    """The chosen turn path."""

    intent: RouteIntent


def is_literal_slash_command(text: str) -> bool:
    """True when the user submitted an explicit ``/slash`` command line."""
    return text.strip().startswith("/")


def routing_input_from_result(
    action_result: ToolCallingTurnResult, observation: str | None
) -> TurnRoutingInput:
    return TurnRoutingInput(
        action_handled=action_result.handled,
        executed_success_count=action_result.executed_success_count,
        has_observation=observation is not None,
        investigation_dispatched=action_result.investigation_dispatched,
    )


def route_turn(
    routing: TurnRoutingInput,
    *,
    user_text: str = "",
    handoff_contents: tuple[str, ...] = (),
) -> TurnRoute:
    """Decide the turn path from routing facts (pure)."""
    if (
        routing.investigation_dispatched
        and routing.action_handled
        and not is_literal_slash_command(user_text)
    ):
        if routing.has_observation and routing.executed_success_count > 0:
            return TurnRoute(intent="summarize_observation")
        return TurnRoute(intent="handled_without_llm")
    if (
        routing.action_handled
        and routing.has_observation
        and routing.executed_success_count > 0
        and not is_literal_slash_command(user_text)
    ):
        return TurnRoute(intent="summarize_observation")
    if routing.action_handled and not handoff_contents:
        return TurnRoute(intent="handled_without_llm")
    # ``/goal set`` attaches a host-owned goal mid-turn and the orchestrator
    # injects ``session_goal:continue`` for Want-me-to suppression. That tag
    # must not force gather on the slash attach turn itself — autosubmit owns
    # the first real metric work turn.
    if routing.action_handled and is_literal_slash_command(user_text):
        return TurnRoute(intent="handled_without_llm")
    return TurnRoute(intent="gather_and_answer")


__all__ = [
    "RouteIntent",
    "TurnRoute",
    "TurnRoutingInput",
    "is_literal_slash_command",
    "route_turn",
    "routing_input_from_result",
]
