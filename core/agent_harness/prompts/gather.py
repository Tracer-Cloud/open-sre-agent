"""System prompt for the evidence-gathering pass of the terminal assistant.

This is the fourth harness-surface prompt builder. It lives here, in the single
``core/agent_harness/prompts/`` home, alongside the action and assistant builders
so every harness prompt is constructed in one place (issue #3434, Problem 2).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from core.agent_harness.models.turn_context import TurnContext
    from core.agent_harness.ports import SessionStore


def build_gather_system_prompt(session: SessionStore) -> str:
    """Build the system prompt for one evidence-gathering turn.

    The gather pass calls read-only integration tools to collect evidence for a
    user question; a later step composes the user-facing answer from what it
    returns. The prompt names the configured integrations so the model scopes its
    tool calls to what is actually connected.
    """
    configured = (
        ", ".join(session.configured_integrations)
        if session.configured_integrations
        else "(unknown)"
    )
    return (
        "You are the data-gathering step of the OpenSRE terminal assistant. The "
        "user asked a question that may be answerable with live data from the "
        "connected integrations. You have access to the same tools the "
        "investigation pipeline uses (logs, metrics, GitHub, error trackers, "
        "cloud APIs, etc.).\n"
        "Call the tools needed to gather evidence relevant to the user's "
        "question. Derive arguments (such as owner/repo, service names, time "
        "ranges, or search queries) from the user's message. Make tool calls "
        "ONLY when they will help answer the question; if no tool is relevant, "
        "respond with a short plain-text note and call nothing.\n"
        "For GitHub repository metadata such as star count, forks, visibility, "
        "or default branch, call get_github_repository — do not use "
        "search_github_code or search_github_issues for those questions.\n"
        "Do NOT write the final user-facing answer here — a later step composes "
        "that from the tool results you collect. Stop calling tools as soon as "
        "you have enough data.\n"
        f"Configured integrations in this session: {configured}."
    )


def build_gather_system_prompt_from_turn_context(turn_ctx: TurnContext) -> str:
    """Build the gather system prompt from a :class:`TurnContext` snapshot.

    Uses the same integration list the action and assistant agents saw at
    turn start. Prefer this when a ``TurnContext`` is already available.
    """

    class _GatherSessionView:
        @property
        def configured_integrations(self) -> tuple[str, ...]:
            return turn_ctx.configured_integrations

    return build_gather_system_prompt(_GatherSessionView())  # type: ignore[arg-type]
