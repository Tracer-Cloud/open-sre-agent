"""Load one action-agent skill body on demand (thin harness / fat skills)."""

from __future__ import annotations

from typing import Any

from core.agent_harness.prompts.skills.loader import list_action_skills, load_skill_body
from core.agent_harness.tools.tool_context import (
    ActionToolContext,
    execute_with_action_context,
    object_schema,
    string_property,
)
from core.tool_framework.registered_tool import RegisteredTool


def execute_skill_view_tool(args: dict[str, Any], ctx: ActionToolContext) -> dict[str, Any]:
    _ = ctx
    name = str(args.get("name", "")).strip()
    if not name:
        available = [skill.name for skill in list_action_skills()]
        return {
            "ok": False,
            "error": "missing skill name",
            "available": available,
        }
    body = load_skill_body(name)
    if not body:
        available = [skill.name for skill in list_action_skills()]
        return {
            "ok": False,
            "name": name,
            "error": f"unknown skill {name!r}",
            "available": available,
        }
    slug = name.replace("_", "-").lower()
    # ``summary`` is what the user sees; ``content`` is for the model only.
    # Without it the generic formatter prints the whole skill body on screen.
    return {
        "ok": True,
        "name": slug,
        "summary": f"loaded the {slug} skill",
        "content": body,
    }


def run_skill_view(*, name: str, context: Any) -> dict[str, Any]:
    return execute_with_action_context({"name": name}, context, execute_skill_view_tool)


skill_view_tool = RegisteredTool(
    name="skill_view",
    description=(
        "Load the full body of one action-agent skill by name from the "
        "SKILLS INDEX. Call this in the same turn when the user request matches "
        "an indexed skill, BEFORE emitting that skill's tool sequence. Do not "
        "invent workflow steps from the one-line index description alone."
    ),
    input_schema=object_schema(
        properties={
            "name": string_property(
                description=(
                    "Skill name from the SKILLS INDEX (kebab-case), e.g. "
                    "'morning-report' or 'architecture-audit'."
                ),
                min_length=1,
            ),
        },
        required=("name",),
    ),
    source="interactive_shell",
    surfaces=("action",),
    parallel_safe=True,
    accepts_runtime_context=True,
    run=run_skill_view,
    tags=("safe", "fast", "no-credentials"),
    side_effect_level="read_only",
)


__all__ = ["execute_skill_view_tool", "run_skill_view", "skill_view_tool"]
