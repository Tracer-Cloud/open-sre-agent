"""Gather-pass system prompt builder."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from core.agent_harness.prompts.prior_investigation import (
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
    configured = (
        ", ".join(session.configured_integrations)
        if session.configured_integrations
        else "(unknown)"
    )
    last_state = getattr(session, "last_state", None)
    prior = _compact_prior_investigation(last_state)
    # Soft guidance for turns that *do* gather. Hard skip for explicit
    # ``follow_up:`` handoffs lives in the orchestrator and is not age-gated.
    rule = (
        f"\n{_PRIOR_INVESTIGATION_GATHER_RULE}\n"
        if is_within_recall_window(last_state)
        else f"\n{_STALE_PRIOR_INVESTIGATION_GATHER_RULE}\n"
    )
    prior_block = f"{rule}--- Prior investigation in this session ---\n{prior}\n" if prior else ""
    prompt = (
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
        "Do NOT write the final user-facing answer here — a later step composes "
        "that from the tool results you collect. Stop calling tools as soon as "
        "you have enough data.\n"
        f"Configured integrations in this session: {configured}."
        f"{prior_block}"
    )
    vendor_fragments = gather_prompt_vendor_fragments()
    if vendor_fragments:
        prompt = f"{prompt}\n{vendor_fragments}"
    return prompt


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
