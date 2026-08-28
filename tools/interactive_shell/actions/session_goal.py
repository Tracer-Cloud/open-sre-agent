"""Attach a structured conversational SessionGoal."""

from __future__ import annotations

from typing import Any

from core.agent_harness.spi.session_goal import (
    attach_session_goal,
    build_session_goal,
    session_goal_is_attached,
)
from core.agent_harness.tools import ActionToolScope, execute_with_action_context
from core.domain.types.tools import ToolSurface
from core.tool import RegisteredTool, SideEffectLevel
from core.tool_framework.utils import object_schema, string_array_property, string_property


def execute_session_goal_tool(args: dict[str, Any], ctx: ActionToolScope) -> dict[str, Any]:
    existing = getattr(ctx.session, "session_goal", None)
    if session_goal_is_attached(ctx.session):
        return {
            "ok": True,
            "attached": False,
            "reason": "an active or paused session goal is already attached",
            "condition": getattr(existing, "condition", ""),
        }

    condition = str(args.get("condition", "")).strip()
    if not condition:
        return {"ok": False, "error": "condition is required"}
    raw_items = args.get("items")
    items = tuple(str(item).strip() for item in raw_items) if isinstance(raw_items, list) else ()
    raw_max_turns = args.get("max_turns")
    max_turns = (
        raw_max_turns
        if isinstance(raw_max_turns, int) and not isinstance(raw_max_turns, bool)
        else None
    )
    goal = attach_session_goal(
        ctx.session,
        build_session_goal(condition, checklist=items, max_outer_turns=max_turns),
    )
    return {
        "ok": True,
        "attached": True,
        "condition": goal.condition,
        "items": list(goal.checklist),
        "max_turns": goal.max_outer_turns,
    }


def run_session_goal(
    *,
    condition: str,
    context: Any,
    items: list[str] | None = None,
    max_turns: int | None = None,
) -> dict[str, Any]:
    return execute_with_action_context(
        {
            "condition": condition,
            "items": items or [],
            "max_turns": max_turns,
        },
        context,
        execute_session_goal_tool,
    )


session_goal_tool = RegisteredTool(
    name="session_goal_set",
    description=(
        "Attach a cross-turn conversational goal for a checklist or walkthrough "
        "the user asked to continue without pausing. Do not use for local shell "
        "work or a single-turn answer."
    ),
    use_cases=[
        (
            "User asks to walk a multi-step checklist or keep going across turns "
            "until a finish condition is met (e.g. a 5-step sequential process)"
        ),
        (
            "Action handoff needs a durable SessionGoal so the host continues "
            "outer turns until the checklist is done"
        ),
    ],
    anti_examples=[
        "One-shot Q&A or a single lookup that finishes this turn",
        "Local shell / code-edit work that should use shell_run or update_plan",
        "User only wants a written plan with no execution (use update_plan)",
    ],
    input_schema=object_schema(
        properties={
            "condition": string_property(
                description="The user's requested completion condition.",
                min_length=1,
            ),
            "items": string_array_property(
                description="Optional checklist items in completion order.",
            ),
            "max_turns": {
                "type": "integer",
                "minimum": 1,
                "description": "Optional hard cap on outer chat turns.",
            },
        },
        required=("condition",),
    ),
    source="interactive_shell",
    surfaces=(ToolSurface.ACTION,),
    side_effect_level=SideEffectLevel.MUTATING,
    parallel_safe=False,
    accepts_runtime_context=True,
    run=run_session_goal,
)


__all__ = ["execute_session_goal_tool", "session_goal_tool"]
