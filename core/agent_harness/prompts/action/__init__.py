"""Tool-calling / slash action-agent system and user prompt assembly."""

from __future__ import annotations

from core.agent_harness.prompts.action.assemble import (
    build_action_system_prompt,
    build_action_system_prompt_envelope,
    build_action_user_message,
    connected_integrations_block,
    prior_action_facts_block,
    recent_conversation_block,
    sanitize_action_text,
)
from core.agent_harness.prompts.action.text import _SYSTEM_PROMPT_BASE

__all__ = [
    "_SYSTEM_PROMPT_BASE",
    "build_action_system_prompt",
    "build_action_system_prompt_envelope",
    "build_action_user_message",
    "connected_integrations_block",
    "prior_action_facts_block",
    "recent_conversation_block",
    "sanitize_action_text",
]
