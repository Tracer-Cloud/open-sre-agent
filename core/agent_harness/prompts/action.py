"""Shell action-planner prompt assembly."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from core.agent_harness.prompts.action_agent_prompt import (
    build_action_system_prompt,
    build_action_system_prompt_envelope,
    build_action_user_message,
    connected_integrations_block,
    recent_conversation_block,
    sanitize_action_text,
)
from core.agent_harness.prompts.envelope import PromptEnvelope

if TYPE_CHECKING:
    from core.agent_harness.models.turn_snapshot import TurnSnapshot


@dataclass(frozen=True)
class ActionPlannerPrompt:
    """Rendered prompt pair sent to the shell action planner."""

    system: str
    user: str


def build_action_planner_prompt(*, turn_snapshot: TurnSnapshot, text: str) -> ActionPlannerPrompt:
    """Build the action planner's system prompt and literal user message."""
    system_envelope = build_action_system_prompt_envelope(turn_snapshot)
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
