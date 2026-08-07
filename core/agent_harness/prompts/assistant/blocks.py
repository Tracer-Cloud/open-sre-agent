"""Block contributors for the assistant system prompt.

Each contributor reads :class:`AssistantPromptParts` and the surface profile,
and returns zero or more :class:`PromptBlock` values. Assembly is an ordered
tuple of these — not a growing kwargs bag with inline ``if`` appends.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence

from core.agent_harness.prompts.assistant.parts import AssistantPromptParts
from core.agent_harness.prompts.assistant.text import (
    CLI_PREAMBLE,
    DOCS_PREAMBLE,
    GATEWAY_PREAMBLE,
    INTERACTION_RULES,
    LONG_TERM_MEMORY_PREAMBLE,
    MARKDOWN_RULE,
    PRIOR_ACTION_FACTS_PREAMBLE,
    PRIOR_INVESTIGATION_FOLLOW_UP_RULE,
    RESPONSE_SHAPE_RULE,
    SETUP_GUIDANCE_RULE,
    SOURCE_SCOPED_INVESTIGATION_RULE,
    TERMINOLOGY_RULE,
)
from core.agent_harness.prompts.kernel.envelope import (
    PromptBlock,
    PromptBlockKind,
    PromptTier,
)
from core.agent_harness.prompts.kernel.surfaces import SurfaceProfile
from platform.harness_ports import assistant_prompt_vendor_fragments, gateway_persona_fragments

_HERE = "core.agent_harness.prompts.assistant.blocks"

Contributor = Callable[[AssistantPromptParts, SurfaceProfile], Sequence[PromptBlock]]


def _block(
    block_id: str,
    content: str,
    *,
    kind: PromptBlockKind,
    tier: PromptTier,
    provenance: str,
) -> PromptBlock:
    return PromptBlock(id=block_id, content=content, kind=kind, tier=tier, provenance=provenance)


def contribute_preamble(parts: AssistantPromptParts, profile: SurfaceProfile) -> list[PromptBlock]:
    _ = parts
    return [
        _block(
            "assistant-preamble",
            CLI_PREAMBLE if profile.cli_rules else GATEWAY_PREAMBLE,
            kind=PromptBlockKind.SYSTEM,
            tier=PromptTier.STABLE,
            provenance=_HERE,
        )
    ]


def contribute_interaction_rules(
    parts: AssistantPromptParts, profile: SurfaceProfile
) -> list[PromptBlock]:
    _ = parts, profile
    return [
        _block(
            "assistant-interaction-rules",
            INTERACTION_RULES,
            kind=PromptBlockKind.RULE,
            tier=PromptTier.STABLE,
            provenance=_HERE,
        ),
        _block(
            "assistant-prior-investigation-rule",
            f"{PRIOR_INVESTIGATION_FOLLOW_UP_RULE}\n\n",
            kind=PromptBlockKind.RULE,
            tier=PromptTier.STABLE,
            provenance=_HERE,
        ),
    ]


def contribute_cli_setup_guidance(
    parts: AssistantPromptParts, profile: SurfaceProfile
) -> list[PromptBlock]:
    _ = parts
    if not profile.cli_rules:
        return []
    return [
        _block(
            "assistant-setup-guidance",
            f"{SETUP_GUIDANCE_RULE}\n\n",
            kind=PromptBlockKind.RULE,
            tier=PromptTier.STABLE,
            provenance=_HERE,
        )
    ]


def contribute_source_scoped_rule(
    parts: AssistantPromptParts, profile: SurfaceProfile
) -> list[PromptBlock]:
    _ = parts, profile
    return [
        _block(
            "assistant-source-scoped-rule",
            f"{SOURCE_SCOPED_INVESTIGATION_RULE}\n\n",
            kind=PromptBlockKind.RULE,
            tier=PromptTier.STABLE,
            provenance=_HERE,
        )
    ]


def contribute_vendor_fragments(
    parts: AssistantPromptParts, profile: SurfaceProfile
) -> list[PromptBlock]:
    _ = parts, profile
    vendor_fragments_text = assistant_prompt_vendor_fragments()
    if not vendor_fragments_text:
        return []
    return [
        _block(
            "assistant-vendor-fragments",
            f"{vendor_fragments_text}\n\n",
            kind=PromptBlockKind.RULE,
            tier=PromptTier.STABLE,
            provenance="platform.harness_ports.assistant_prompt_vendor_fragments",
        )
    ]


def contribute_cli_response_shape(
    parts: AssistantPromptParts, profile: SurfaceProfile
) -> list[PromptBlock]:
    _ = parts
    if not profile.cli_rules:
        return []
    return [
        _block(
            "assistant-response-shape",
            f"{RESPONSE_SHAPE_RULE}\n\n",
            kind=PromptBlockKind.RULE,
            tier=PromptTier.STABLE,
            provenance=_HERE,
        )
    ]


def contribute_gateway_persona(
    parts: AssistantPromptParts, profile: SurfaceProfile
) -> list[PromptBlock]:
    _ = parts
    # Gateway persona wording is vendor-owned and reached only through the port
    # — see integrations.slack.gateway_persona. It replaces the CLI-shaped rules
    # above wholesale rather than adding to them.
    if not profile.vendor_persona:
        return []
    return [
        _block(
            "assistant-gateway-persona",
            f"{gateway_persona_fragments()}\n\n",
            kind=PromptBlockKind.RULE,
            tier=PromptTier.STABLE,
            provenance="platform.harness_ports.gateway_persona_fragments",
        )
    ]


def contribute_cli_terminology(
    parts: AssistantPromptParts, profile: SurfaceProfile
) -> list[PromptBlock]:
    _ = parts
    if not profile.cli_rules:
        return []
    return [
        _block(
            "assistant-terminology",
            f"{TERMINOLOGY_RULE}\n",
            kind=PromptBlockKind.RULE,
            tier=PromptTier.STABLE,
            provenance=_HERE,
        )
    ]


def contribute_markdown_rule(
    parts: AssistantPromptParts, profile: SurfaceProfile
) -> list[PromptBlock]:
    _ = parts, profile
    return [
        _block(
            "assistant-markdown-rule",
            f"{MARKDOWN_RULE}\n\n",
            kind=PromptBlockKind.RULE,
            tier=PromptTier.STABLE,
            provenance=_HERE,
        )
    ]


def contribute_cli_reference(
    parts: AssistantPromptParts, profile: SurfaceProfile
) -> list[PromptBlock]:
    _ = profile
    return [
        _block(
            "assistant-cli-reference",
            f"--- CLI reference ---\n{parts.reference}\n\n",
            kind=PromptBlockKind.CONTEXT,
            tier=PromptTier.STABLE,
            provenance="core.agent_harness.ports.PromptContextProvider.cli_reference",
        )
    ]


def contribute_investigation_flow(
    parts: AssistantPromptParts, profile: SurfaceProfile
) -> list[PromptBlock]:
    _ = profile
    if not parts.investigation_flow:
        return []
    return [
        _block(
            "assistant-investigation-flow",
            f"--- Investigation flow reference ---\n{parts.investigation_flow}\n\n",
            kind=PromptBlockKind.CONTEXT,
            tier=PromptTier.STABLE,
            provenance="core.agent_harness.grounding.investigation_flow_reference",
        )
    ]


def contribute_environment(
    parts: AssistantPromptParts, profile: SurfaceProfile
) -> list[PromptBlock]:
    _ = profile
    if not parts.environment:
        return []
    return [
        _block(
            "assistant-environment",
            parts.environment,
            kind=PromptBlockKind.CONTEXT,
            tier=PromptTier.CONTEXT,
            provenance="core.agent_harness.prompts.assistant.environment",
        )
    ]


def contribute_setup_state(
    parts: AssistantPromptParts, profile: SurfaceProfile
) -> list[PromptBlock]:
    if not (parts.setup_state and profile.setup_state):
        return []
    return [
        _block(
            "assistant-setup-state",
            parts.setup_state,
            kind=PromptBlockKind.CONTEXT,
            tier=PromptTier.CONTEXT,
            provenance="platform.setup_state",
        )
    ]


def contribute_repo_map(parts: AssistantPromptParts, profile: SurfaceProfile) -> list[PromptBlock]:
    _ = profile
    if not parts.agents_md:
        return []
    return [
        _block(
            "assistant-repo-map",
            f"--- Repo map (AGENTS.md) ---\n{parts.agents_md}\n\n",
            kind=PromptBlockKind.CONTEXT,
            tier=PromptTier.CONTEXT,
            provenance="core.agent_harness.grounding.agents_md",
        )
    ]


def contribute_prior_investigation(
    parts: AssistantPromptParts, profile: SurfaceProfile
) -> list[PromptBlock]:
    _ = profile
    if not parts.prior_investigation:
        return []
    return [
        _block(
            "assistant-prior-investigation",
            f"--- Prior investigation in this session ---\n{parts.prior_investigation}\n\n",
            kind=PromptBlockKind.CONTEXT,
            tier=PromptTier.VOLATILE,
            provenance="core.agent_harness.prompts.memory.prior_investigation",
        )
    ]


def contribute_long_term_memory(
    parts: AssistantPromptParts, profile: SurfaceProfile
) -> list[PromptBlock]:
    _ = profile
    if not parts.long_term_memory:
        return []
    return [
        _block(
            "assistant-long-term-memory",
            LONG_TERM_MEMORY_PREAMBLE + parts.long_term_memory + "\n\n",
            kind=PromptBlockKind.CONTEXT,
            tier=PromptTier.VOLATILE,
            provenance="core.domain.memory",
        )
    ]


def contribute_docs(parts: AssistantPromptParts, profile: SurfaceProfile) -> list[PromptBlock]:
    _ = profile
    if not parts.docs:
        return []
    return [
        _block(
            "assistant-docs",
            DOCS_PREAMBLE + parts.docs + "\n\n",
            kind=PromptBlockKind.CONTEXT,
            tier=PromptTier.EPHEMERAL,
            provenance="core.agent_harness.grounding.docs",
        )
    ]


def contribute_prior_action_facts(
    parts: AssistantPromptParts, profile: SurfaceProfile
) -> list[PromptBlock]:
    _ = profile
    if not parts.prior_action_facts:
        return []
    return [
        _block(
            "assistant-prior-action-facts",
            PRIOR_ACTION_FACTS_PREAMBLE + parts.prior_action_facts + "\n\n",
            kind=PromptBlockKind.CONTEXT,
            tier=PromptTier.EPHEMERAL,
            provenance="core.agent_harness.turns.turn_snapshot",
        )
    ]


def contribute_recent_conversation(
    parts: AssistantPromptParts, profile: SurfaceProfile
) -> list[PromptBlock]:
    _ = profile
    return [
        _block(
            "assistant-recent-conversation",
            f"--- Recent CLI conversation ---\n{parts.history}\n",
            kind=PromptBlockKind.CONVERSATION,
            tier=PromptTier.EPHEMERAL,
            provenance="core.agent_harness.turns.turn_snapshot",
        )
    ]


#: Ordered contributors. Surface policy lives in :class:`SurfaceProfile`; each
#: contributor decides from parts + profile whether to emit blocks.
ASSISTANT_BLOCK_CONTRIBUTORS: tuple[Contributor, ...] = (
    contribute_preamble,
    contribute_interaction_rules,
    contribute_cli_setup_guidance,
    contribute_source_scoped_rule,
    contribute_vendor_fragments,
    contribute_cli_response_shape,
    contribute_gateway_persona,
    contribute_cli_terminology,
    contribute_markdown_rule,
    contribute_cli_reference,
    contribute_investigation_flow,
    contribute_environment,
    contribute_setup_state,
    contribute_repo_map,
    contribute_prior_investigation,
    contribute_long_term_memory,
    contribute_docs,
    contribute_prior_action_facts,
    contribute_recent_conversation,
)

__all__ = ["ASSISTANT_BLOCK_CONTRIBUTORS", "Contributor"]
