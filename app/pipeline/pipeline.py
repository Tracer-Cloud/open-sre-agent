"""Raw-alert-first connected investigation coordinator."""

from __future__ import annotations

import logging
from typing import Any, cast

from app.state import AgentState

logger = logging.getLogger(__name__)


def run_connected_investigation(state: AgentState) -> AgentState:
    """Resolve connected integrations → parse alert → agent loop → deliver.

    All steps mutate a shared state dict. Each step returns a dict of updates
    which are merged in. Pure function: inputs in, state out.
    """
    from app.agent.context import resolve_integrations
    from app.agent.extract import extract_alert
    from app.agent.investigation import ConnectedInvestigationAgent
    from app.delivery import deliver
    from app.utils.sentry_sdk import capture_exception

    state_any = cast(dict[str, Any], state)

    try:
        _merge(state_any, {"resolved_integrations": resolve_integrations(state)})

        _merge(state_any, extract_alert(state))
        if state_any.get("is_noise"):
            return cast(AgentState, state_any)

        _merge(state_any, ConnectedInvestigationAgent().run(state_any))

        _merge(state_any, deliver(state))
    except Exception as exc:
        capture_exception(exc)
        raise

    return cast(AgentState, state_any)


def run_investigation(state: AgentState) -> AgentState:
    """Backward-compatible alias for the connected investigation coordinator."""
    return run_connected_investigation(state)


def run_chat(state: AgentState) -> AgentState:
    """Run a single chat turn via ChatAgent."""
    from app.agent.chat import ChatAgent
    from app.utils.sentry_sdk import capture_exception

    state_any = cast(dict[str, Any], state)
    try:
        updates = ChatAgent().run(state)
        _merge(state_any, updates)
    except Exception as exc:
        capture_exception(exc)
        raise
    return cast(AgentState, state_any)


def _merge(state: dict[str, Any], updates: dict[str, Any]) -> None:
    if not updates:
        return
    for key, value in updates.items():
        if key == "messages":
            messages = list(state.get("messages") or [])
            if isinstance(value, list):
                messages.extend(value)
            else:
                messages.append(value)
            state["messages"] = messages
        else:
            state[key] = value
