"""Prompt builders for the decoupled agentic turn engine."""

from __future__ import annotations

from core.agent_harness.prompts.action import ActionPlannerPrompt, build_action_planner_prompt
from core.agent_harness.prompts.action_agent_prompt import (
    build_action_system_prompt,
    build_action_system_prompt_envelope,
    build_action_user_message,
    connected_integrations_block,
    recent_conversation_block,
    sanitize_action_text,
)
from core.agent_harness.prompts.action_agent_system_prompt import _SYSTEM_PROMPT_BASE
from core.agent_harness.prompts.assistant import (
    AssistantPromptContextProvider,
    ShellPromptSession,
    build_assistant_system_prompt,
    build_cli_agent_prompt,
    build_cli_agent_prompt_envelope,
    build_cli_agent_prompt_from_provider,
    build_observation_block,
    build_shell_environment_block,
)
from core.agent_harness.prompts.assistant_agent_prompt import (
    _build_observation_block,
    _build_system_prompt,
    build_environment_block,
)
from core.agent_harness.prompts.envelope import PromptBlock, PromptEnvelope

__all__ = [
    "_SYSTEM_PROMPT_BASE",
    "_build_observation_block",
    "_build_system_prompt",
    "ActionPlannerPrompt",
    "AssistantPromptContextProvider",
    "PromptBlock",
    "PromptEnvelope",
    "ShellPromptSession",
    "build_action_planner_prompt",
    "build_action_system_prompt",
    "build_action_system_prompt_envelope",
    "build_action_user_message",
    "build_assistant_system_prompt",
    "build_cli_agent_prompt",
    "build_cli_agent_prompt_envelope",
    "build_cli_agent_prompt_from_provider",
    "build_environment_block",
    "build_observation_block",
    "build_shell_environment_block",
    "connected_integrations_block",
    "recent_conversation_block",
    "sanitize_action_text",
]
