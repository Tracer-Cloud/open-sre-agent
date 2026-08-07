"""Assemble the assistant system prompt from parts + surface profile.

Deep module: callers pass :class:`AssistantPromptParts`; contributors and the
surface Strategy table (:class:`~core.agent_harness.prompts.kernel.surfaces.SurfaceProfile`)
stay inside.
"""

from __future__ import annotations

from core.agent_harness.prompts.assistant.blocks import ASSISTANT_BLOCK_CONTRIBUTORS
from core.agent_harness.prompts.assistant.parts import AssistantPromptParts
from core.agent_harness.prompts.kernel.envelope import PromptBlock, PromptEnvelope
from core.agent_harness.prompts.kernel.surfaces import profile_for


def assemble_assistant_envelope(parts: AssistantPromptParts) -> PromptEnvelope:
    """Build the tiered assistant system prompt from ``parts``."""
    profile = profile_for(parts.surface)
    blocks: list[PromptBlock] = []
    for contribute in ASSISTANT_BLOCK_CONTRIBUTORS:
        blocks.extend(contribute(parts, profile))
    return PromptEnvelope.from_blocks(blocks, separator="", metadata={"prompt": "assistant_system"})


def build_assistant_system_prompt_envelope(
    reference: str,
    history: str,
    agents_md: str = "",
    docs: str = "",
    investigation_flow: str = "",
    prior_investigation: str = "",
    prior_action_facts: str = "",
    environment: str = "",
    long_term_memory: str = "",
    setup_state: str = "",
    surface: str = "interactive_shell",
) -> PromptEnvelope:
    """Assemble the assistant prompt as tiered blocks.

    Prefer :func:`assemble_assistant_envelope` with an
    :class:`AssistantPromptParts` at new call sites. This kwargs form remains
    for existing tests and characterization harnesses.
    """
    return assemble_assistant_envelope(
        AssistantPromptParts(
            reference=reference,
            history=history,
            agents_md=agents_md,
            docs=docs,
            investigation_flow=investigation_flow,
            prior_investigation=prior_investigation,
            prior_action_facts=prior_action_facts,
            environment=environment,
            long_term_memory=long_term_memory,
            setup_state=setup_state,
            surface=surface,
        )
    )


def build_assistant_system_prompt(
    reference: str,
    history: str,
    agents_md: str = "",
    docs: str = "",
    investigation_flow: str = "",
    prior_investigation: str = "",
    prior_action_facts: str = "",
    environment: str = "",
    long_term_memory: str = "",
    setup_state: str = "",
    surface: str = "interactive_shell",
) -> str:
    """Build the system prompt for one assistant turn as a single string."""
    return build_assistant_system_prompt_envelope(
        reference,
        history,
        agents_md=agents_md,
        docs=docs,
        investigation_flow=investigation_flow,
        prior_investigation=prior_investigation,
        prior_action_facts=prior_action_facts,
        environment=environment,
        long_term_memory=long_term_memory,
        setup_state=setup_state,
        surface=surface,
    ).render()


# Legacy private name used by older tests.
_build_system_prompt = build_assistant_system_prompt

__all__ = [
    "_build_system_prompt",
    "assemble_assistant_envelope",
    "build_assistant_system_prompt",
    "build_assistant_system_prompt_envelope",
]
