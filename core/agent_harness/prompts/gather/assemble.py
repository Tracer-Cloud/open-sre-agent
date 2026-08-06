"""Gather-pass system prompt builder."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from core.agent_harness.prompts.kernel.envelope import (
    PromptBlock,
    PromptBlockKind,
    PromptEnvelope,
    PromptTier,
)
from core.agent_harness.prompts.memory.prior_investigation import (
    is_within_recall_window,
    prior_investigation_headline,
)
from platform.harness_ports import gather_prompt_vendor_fragments

if TYPE_CHECKING:
    from core.agent_harness.ports import SessionStore
    from core.agent_harness.turns.turn_snapshot import TurnSnapshot

_PRIOR_INVESTIGATION_GATHER_RULE = (
    "Prior investigation in this session: when the block below is present and "
    "the user asks a retrospective question about that investigation (for "
    "example 'what happened?', 'what was the root cause?', 'what caused the "
    "spike?', 'why did it fail?', or 'during the last investigation'), call "
    "NO tools — a later step answers from that prior investigation data. Only "
    "call tools when the question clearly needs fresh live data beyond what "
    "the prior investigation already concluded."
)

_STALE_PRIOR_INVESTIGATION_GATHER_RULE = (
    "Prior investigation in this session: the block below is from earlier in "
    "this session and may no longer reflect current state. Use it as background "
    "for what the user is referring to, but DO gather fresh data for the "
    "question being asked rather than assuming the earlier conclusion still holds."
)


def _compact_prior_investigation(state: dict[str, Any] | None) -> str:
    """Compact last-investigation facts for the gather prompt (not the full report)."""
    if not state:
        return ""
    return "\n".join(prior_investigation_headline(state))


_GATHER_BASE = (
    "You are the data-gathering step of the OpenSRE terminal assistant. The "
    "user asked a question that may be answerable with live data from the "
    "connected integrations. You have access to the same tools the "
    "investigation pipeline uses (logs, metrics, VCS, error trackers, "
    "cloud APIs, etc.).\n"
    "Call the tools needed to gather evidence relevant to the user's "
    "question. Derive arguments (such as owner/repo, service names, time "
    "ranges, or search queries) from the user's message. Make tool calls "
    "ONLY when they will help answer the question; if no tool is relevant, "
    "respond with a short plain-text note and call nothing.\n"
    "Prefer finding the answer with tools over leaving the question for the "
    "user: when the question names or implies a configured data source, "
    "query it rather than assuming a later step will ask the user for the "
    "data. If a first query returns nothing useful, refine it once (tighter "
    "service name, wider time range, alternate search term) before giving up.\n"
    "Do NOT write the final user-facing answer here — a later step composes "
    "that from the tool results you collect. Stop calling tools as soon as "
    "you have enough data.\n"
)


def build_gather_system_prompt_envelope(session: SessionStore) -> PromptEnvelope:
    """Assemble the gather prompt as tiered blocks.

    Same layering as the action prompt: instructions and vendor recipes are
    stable for the process, the connected-integration list is session context,
    and a prior investigation changes only when one completes.
    """
    blocks = [
        PromptBlock(
            id="gather-system-base",
            kind=PromptBlockKind.SYSTEM,
            tier=PromptTier.STABLE,
            content=_GATHER_BASE,
            provenance="core.agent_harness.prompts.gather.assemble",
        )
    ]
    vendor_fragments = gather_prompt_vendor_fragments()
    if vendor_fragments:
        blocks.append(
            PromptBlock(
                id="gather-vendor-fragments",
                kind=PromptBlockKind.RULE,
                tier=PromptTier.STABLE,
                content=f"{vendor_fragments}\n",
                provenance="platform.harness_ports.gather_prompt_vendor_fragments",
            )
        )
    configured = (
        ", ".join(session.configured_integrations)
        if session.configured_integrations
        else "(unknown)"
    )
    blocks.append(
        PromptBlock(
            id="gather-connected-integrations",
            kind=PromptBlockKind.CONTEXT,
            tier=PromptTier.CONTEXT,
            content=f"Configured integrations in this session: {configured}.",
            provenance="core.agent_harness.ports.SessionStore",
        )
    )
    last_state = getattr(session, "last_state", None)
    prior = _compact_prior_investigation(last_state)
    if prior:
        # Soft guidance for turns that *do* gather. Hard skip for explicit
        # ``follow_up:`` handoffs lives in the orchestrator and is not age-gated.
        rule = (
            _PRIOR_INVESTIGATION_GATHER_RULE
            if is_within_recall_window(last_state)
            else _STALE_PRIOR_INVESTIGATION_GATHER_RULE
        )
        blocks.append(
            PromptBlock(
                id="gather-prior-investigation",
                kind=PromptBlockKind.CONTEXT,
                tier=PromptTier.VOLATILE,
                content=(f"\n{rule}\n--- Prior investigation in this session ---\n{prior}\n"),
                provenance="core.agent_harness.prompts.memory.prior_investigation",
            )
        )
    return PromptEnvelope.from_blocks(blocks, separator="", metadata={"prompt": "gather_system"})


def build_gather_system_prompt(session: SessionStore) -> str:
    """Build the system prompt for one evidence-gathering turn.

    The gather pass calls read-only integration tools to collect evidence for a
    user question; a later step composes the user-facing answer from what it
    returns. The prompt names the configured integrations so the model scopes its
    tool calls to what is actually connected. Vendor-specific tool-usage recipes
    (which tool to call for a given integration's questions) are supplied by
    registered fragments (see :func:`platform.harness_ports.gather_prompt_vendor_fragments`)
    rather than hardcoded here.
    """
    return build_gather_system_prompt_envelope(session).render()


def build_gather_system_prompt_from_turn_snapshot(turn_snapshot: TurnSnapshot) -> str:
    """Same as :func:`build_gather_system_prompt`, from a turn snapshot."""

    class _GatherSessionView:
        @property
        def configured_integrations(self) -> tuple[str, ...]:
            return turn_snapshot.configured_integrations

        @property
        def last_state(self) -> dict[str, Any] | None:
            return turn_snapshot.last_state

    return build_gather_system_prompt(_GatherSessionView())  # type: ignore[arg-type]
