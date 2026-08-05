"""Prompt builders for the decoupled agentic turn engine.

Subpackages:

* ``assistant/`` — conversational assistant (parts → contributors → envelope)
* ``context/`` — grounding providers that feed assemblers
* ``envelope`` / ``surfaces`` / ``rules`` — shared types and surface Strategy table
"""

from __future__ import annotations

from core.agent_harness.prompts.action_agent_prompt import (
    build_action_system_prompt,
    build_action_system_prompt_envelope,
    build_action_user_message,
    connected_integrations_block,
    prior_action_facts_block,
    recent_conversation_block,
    sanitize_action_text,
)
from core.agent_harness.prompts.action_agent_system_prompt import _SYSTEM_PROMPT_BASE
from core.agent_harness.prompts.assistant import (
    AssistantPromptContextProvider,
    AssistantPromptParts,
    AssistantTurnPrompt,
    _build_observation_block,
    _build_system_prompt,
    assemble_assistant_envelope,
    build_assistant_system_prompt,
    build_assistant_system_prompt_envelope,
    build_cli_agent_prompt_from_provider,
    build_cli_agent_turn_prompt,
    build_environment_block,
    build_observation_block,
)
from core.agent_harness.prompts.envelope import (
    PromptBlock,
    PromptBlockKind,
    PromptEnvelope,
    PromptTier,
)
from core.agent_harness.prompts.gather import (
    build_gather_system_prompt,
    build_gather_system_prompt_envelope,
    build_gather_system_prompt_from_turn_snapshot,
)
from core.agent_harness.prompts.skills_loader import (
    SKILLS_HEADER,
    list_action_skills,
    load_skill_body,
    load_skills_block,
    load_skills_index,
    skills_dir,
)
from core.agent_harness.prompts.surfaces import (
    PromptSurface,
    SurfaceProfile,
    profile_for,
)

__all__ = [
    "SKILLS_HEADER",
    "_SYSTEM_PROMPT_BASE",
    "_build_observation_block",
    "_build_system_prompt",
    "AssistantPromptContextProvider",
    "AssistantPromptParts",
    "AssistantTurnPrompt",
    "PromptBlock",
    "PromptBlockKind",
    "PromptEnvelope",
    "PromptSurface",
    "PromptTier",
    "SurfaceProfile",
    "assemble_assistant_envelope",
    "build_action_system_prompt",
    "profile_for",
    "build_action_system_prompt_envelope",
    "build_action_user_message",
    "build_assistant_system_prompt",
    "build_assistant_system_prompt_envelope",
    "build_gather_system_prompt",
    "build_gather_system_prompt_envelope",
    "build_gather_system_prompt_from_turn_snapshot",
    "build_cli_agent_prompt_from_provider",
    "build_cli_agent_turn_prompt",
    "build_environment_block",
    "build_observation_block",
    "connected_integrations_block",
    "list_action_skills",
    "load_skill_body",
    "load_skills_block",
    "load_skills_index",
    "prior_action_facts_block",
    "recent_conversation_block",
    "sanitize_action_text",
    "skills_dir",
]
