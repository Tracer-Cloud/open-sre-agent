"""Pipeline routing logic — conditional edges and flow control."""

from __future__ import annotations

import logging

from langgraph.constants import Send

from app.investigation_constants import MAX_INVESTIGATION_LOOPS
from app.output import debug_print
from app.state import AgentState, InvestigationState

logger = logging.getLogger(__name__)


def route_by_mode(state: AgentState) -> str:
    """Route based on agent mode. Defaults to chat when mode is not set.

    Emits a WARNING when ``mode`` is absent so misconfigured callers are visible
    in logs rather than silently receiving a chat response instead of an
    investigation.
    """
    mode = state.get("mode")
    if mode is None:
        logger.warning(
            "route_by_mode: 'mode' key is not set in agent state — defaulting to 'chat'. "
            "Set state['mode'] = 'investigation' to trigger the investigation pipeline."
        )
    elif mode not in ("investigation", "chat"):
        logger.warning(
            "route_by_mode: unrecognised mode %r — defaulting to 'chat'. "
            "Valid values are 'investigation' and 'chat'.",
            mode,
        )
    return "investigation" if mode == "investigation" else "chat"


def route_chat(state: AgentState) -> str:
    """Route chat messages by intent."""
    return "tracer_data" if state.get("route") == "tracer_data" else "general"


def route_after_extract(state: AgentState) -> str:
    """Route after alert extraction — skip investigation if noise."""
    return "end" if state.get("is_noise") else "investigate"


def route_investigation_loop(state: AgentState) -> str:
    """Decide whether to continue investigation loop, run OpenRCA eval, or publish."""
    nxt = should_continue_investigation(state)
    if nxt == "investigate":
        return "investigate"
    if state.get("opensre_evaluate") and (state.get("opensre_eval_rubric") or "").strip():
        return "opensre_eval"
    return "publish"


def should_call_tools(state: AgentState) -> str:
    """Check if the last AI message has tool calls that need execution."""
    msgs = list(state.get("messages", []))
    if msgs:
        last = msgs[-1]
        if hasattr(last, "tool_calls") and getattr(last, "tool_calls", None):
            return "call_tools"
    return "done"


def distribute_hypotheses(state: AgentState) -> list[Send] | list[str]:
    """Distribute planned actions to parallel hypothesis execution nodes."""
    actions = state.get("planned_actions", [])
    available_sources = state.get("available_sources", {})
    if not actions:
        # No actions planned, skip to merge
        return ["merge_hypothesis_results"]

    return [
        Send(
            "investigate_hypothesis",
            {"action_to_run": action, "available_sources": available_sources},
        )
        for action in actions
    ]


def should_continue_investigation(state: InvestigationState) -> str:
    """Decide whether to continue investigation or publish findings.

    Loops back to investigate while recommendations exist and loop limit is not reached.
    Publishes findings when recommendations are exhausted, max loops exceeded, or no
    actions are available.

    Error handling rationale:
    - ``KeyError`` / ``TypeError``: state keys missing or wrong type → safe to default
      to "publish" because we cannot determine loop progress anyway.
    - Any other exception is re-raised so LangGraph surfaces it as a graph error
      rather than silently emitting a partial report that looks like a completed run.
    """
    try:
        investigation_recommendations = state.get("investigation_recommendations", [])
        loop_count = state.get("investigation_loop_count", 0)
        available_action_names = state.get("available_action_names", [])
    except (KeyError, TypeError) as e:
        # State is structurally malformed — cannot route, safe to publish empty findings.
        logger.warning(
            "should_continue_investigation: state access failed (%s: %s), publishing",
            type(e).__name__,
            e,
        )
        return "publish"

    if not available_action_names:
        debug_print("No available actions -> publish (safety check)")
        return "publish"

    if loop_count > MAX_INVESTIGATION_LOOPS:
        debug_print(f"Max loops ({MAX_INVESTIGATION_LOOPS}) exceeded -> publish")
        return "publish"

    if investigation_recommendations:
        debug_print(
            f"Has recommendations -> investigate (loop {loop_count}/{MAX_INVESTIGATION_LOOPS})"
        )
        return "investigate"

    return "publish"
