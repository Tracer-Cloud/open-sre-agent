"""Top-level planner orchestration for LLM-driven action plans."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from app.cli.interactive_shell.routing.handle_message_with_agent.orchestration.interaction_models import (
    PlannedAction,
)

from .llm_client import _call_llm
from .parsing import _parse_tool_plan
from .postprocessing import (
    _fail_closed_vague_local_model,
    finalize_planner_result_with_trace,
)
from .prompting import _sanitise_text


@dataclass(frozen=True)
class LlmActionPlanResult:
    """Structured result for one LLM planning pass with postprocess trace."""

    actions: tuple[PlannedAction, ...]
    has_unhandled_clause: bool
    policy_trace: tuple[str, ...]


def plan_actions_with_llm_result(
    message: str,
    *,
    session: Any | None = None,
) -> LlmActionPlanResult | None:
    """Plan actions and return typed policy trace metadata."""
    sanitised = _sanitise_text(message.strip())
    early = _fail_closed_vague_local_model(sanitised)
    if early is not None:
        actions, has_unhandled = early
        return LlmActionPlanResult(
            actions=tuple(actions),
            has_unhandled_clause=has_unhandled,
            policy_trace=("fail_closed_vague_local_model",),
        )

    raw = _call_llm(sanitised, session)
    if raw is None:
        return None

    parsed = _parse_tool_plan(raw, session=session)
    if parsed is None:
        return None
    actions, has_unhandled = parsed
    finalized = finalize_planner_result_with_trace(
        sanitised,
        actions,
        has_unhandled,
        session=session,
    )
    return LlmActionPlanResult(
        actions=tuple(finalized.actions),
        has_unhandled_clause=finalized.has_unhandled,
        policy_trace=tuple(tag.value for tag in finalized.applied_policies),
    )


def plan_actions_with_llm(
    message: str,
    *,
    session: Any | None = None,
) -> tuple[list[PlannedAction], bool] | None:
    """Plan actions from *message* using native tool-calling."""
    planned = plan_actions_with_llm_result(message, session=session)
    if planned is None:
        return None
    return list(planned.actions), planned.has_unhandled_clause
