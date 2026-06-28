"""Shell action-planner prompt assembly."""

from __future__ import annotations

from dataclasses import dataclass

from core.agent.prompts import (
    PromptEnvelope,
    build_action_system_prompt,
    build_action_system_prompt_envelope,
    build_action_user_message,
    connected_integrations_block,
    recent_conversation_block,
    sanitize_action_text,
)
from core.agent.turn_context import TurnContext


@dataclass(frozen=True)
class ActionPlannerPrompt:
    """Rendered prompt pair sent to the shell action planner."""

    system: str
    user: str


def build_action_planner_prompt(*, turn_ctx: TurnContext, text: str) -> ActionPlannerPrompt:
    """Build the action planner's system prompt and literal user message."""
    system_envelope = build_action_system_prompt_envelope(turn_ctx)
    return ActionPlannerPrompt(
        system=system_envelope.render(),
        user=build_action_user_message(text),
    )


__all__ = [
    "ActionPlannerPrompt",
    "PromptEnvelope",
    "build_action_planner_prompt",
    "build_action_system_prompt",
    "build_action_system_prompt_envelope",
    "build_action_user_message",
    "connected_integrations_block",
    "recent_conversation_block",
    "sanitize_action_text",
]
