"""Assistant prompt assembly: parts → contributors → envelope → turn.

Package layout (read top-down):

* ``parts`` — :class:`AssistantPromptParts`, the grounding bag
* ``blocks`` — ordered contributors (surface policy via ``SurfaceProfile``)
* ``assemble`` — deep seam: parts → :class:`PromptEnvelope`
* ``turn`` — provider → cache-split system/user messages
* ``environment`` / ``observation`` / ``text`` — leaf producers and constants
"""

from __future__ import annotations

from core.agent_harness.prompts.assistant.assemble import (
    _build_system_prompt,
    assemble_assistant_envelope,
    build_assistant_system_prompt,
    build_assistant_system_prompt_envelope,
)
from core.agent_harness.prompts.assistant.environment import build_environment_block
from core.agent_harness.prompts.assistant.observation import (
    _build_observation_block,
    build_handoff_guidance_block,
    build_observation_block,
)
from core.agent_harness.prompts.assistant.parts import AssistantPromptParts
from core.agent_harness.prompts.assistant.text import (
    MARKDOWN_RULE,
    TERMINOLOGY_RULE,
)
from core.agent_harness.prompts.assistant.turn import (
    AssistantPromptContextProvider,
    AssistantTurnPrompt,
    build_cli_agent_prompt_from_provider,
    build_cli_agent_turn_prompt,
)

__all__ = [
    "AssistantPromptContextProvider",
    "AssistantPromptParts",
    "AssistantTurnPrompt",
    "MARKDOWN_RULE",
    "TERMINOLOGY_RULE",
    "_build_observation_block",
    "_build_system_prompt",
    "assemble_assistant_envelope",
    "build_assistant_system_prompt",
    "build_assistant_system_prompt_envelope",
    "build_cli_agent_prompt_from_provider",
    "build_cli_agent_turn_prompt",
    "build_environment_block",
    "build_handoff_guidance_block",
    "build_observation_block",
]
