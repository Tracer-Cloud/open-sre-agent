"""Prompt builders for the decoupled agentic turn engine.

Subpackages (by agent path — see ``prompts/AGENTS.md``):

* ``kernel/`` — shared types: ``PromptEnvelope``, tiers, ``SurfaceProfile``
  (no agent-path knowledge)
* ``grounding/`` — prompt-side grounding providers
  (``DefaultPromptContextProvider``); distinct from harness ``grounding/``
* ``assistant/`` — conversational answer path (parts → contributors →
  envelope → turn)
* ``action/`` — tool-calling / slash action-agent system + user prompts
* ``gather/`` — evidence-gather system prompts
* ``memory/`` — conversation window + prior-investigation recall fragments
* ``runtime_facts/`` — runtime-metadata fact lines for prompt assembly
* ``skills/`` — progressive skill index (thin) + markdown bodies on demand

Root modules: ``rules.py`` (shared rule fragments), ``synthetic_failure.py``.
"""

from __future__ import annotations

from core.agent_harness.prompts.action import (
    _SYSTEM_PROMPT_BASE,
    build_action_system_prompt,
    build_action_system_prompt_envelope,
    build_action_user_message,
    connected_integrations_block,
    prior_action_facts_block,
    recent_conversation_block,
    sanitize_action_text,
)
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
from core.agent_harness.prompts.gather import (
    build_gather_system_prompt,
    build_gather_system_prompt_envelope,
    build_gather_system_prompt_from_turn_snapshot,
)
from core.agent_harness.prompts.kernel import (
    PromptBlock,
    PromptBlockKind,
    PromptEnvelope,
    PromptSurface,
    PromptTier,
    SurfaceProfile,
    profile_for,
)
from core.agent_harness.prompts.skills import (
    SKILLS_HEADER,
    list_action_skills,
    load_skill_body,
    load_skills_block,
    load_skills_index,
    skills_dir,
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
