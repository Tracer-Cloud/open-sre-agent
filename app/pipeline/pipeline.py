"""Raw-alert-first connected investigation coordinator."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any, cast

from app.agent.correlation import build_upstream_evidence_provider
from app.state import AgentState

if TYPE_CHECKING:
    # Type-only import — avoids paying the agent module's heavy import cost
    # at pipeline load while still letting static type-checkers validate
    # ``agent_class`` injections.
    from app.agent.stages.investigate import ConnectedInvestigationAgent

logger = logging.getLogger(__name__)


def _build_correlation_config(state: dict[str, Any]) -> dict[str, Any] | None:
    """Return the runtime config carrying the upstream-evidence provider.

    The vendor-specific provider construction lives in
    :mod:`app.agent.correlation`; this function only wraps the result
    in the ``{"configurable": ...}`` shape the correlation node expects.
    Keeping the wrapping here (and not in the correlation package)
    means correlation stays decoupled from the pipeline's runtime
    contract.
    """
    provider = build_upstream_evidence_provider(state)
    if provider is None:
        return None
    return {"configurable": {"upstream_evidence_provider": provider}}


def run_connected_investigation(
    state: AgentState,
    *,
    agent_class: type[ConnectedInvestigationAgent] | None = None,
) -> AgentState:
    """Resolve connected integrations → parse alert → investigate → diagnose → deliver.

    All steps mutate a shared state dict. Each step returns a dict of updates
    which are merged in. Pure function: inputs in, state out.

    ``agent_class``: optional override for the investigation agent class.
    Defaults to :class:`ConnectedInvestigationAgent`. Callers that need a
    custom termination policy, structured-stage progression, or other
    agent-level extensions can pass a subclass instead.
    """
    from app.agent.correlation.node import node_correlate_upstream
    from app.agent.stages.diagnose import diagnose
    from app.agent.stages.extract_alert import extract_alert
    from app.agent.stages.investigate import ConnectedInvestigationAgent
    from app.agent.stages.plan_actions import plan_actions
    from app.agent.stages.publish_findings import deliver
    from app.agent.stages.resolve_integrations import resolve_integrations
    from app.utils.sentry_sdk import capture_exception

    agent_class = agent_class or ConnectedInvestigationAgent
    state_any = cast(dict[str, Any], state)

    try:
        _merge(state_any, {"resolved_integrations": resolve_integrations(state)})

        _merge(state_any, extract_alert(state))
        if state_any.get("is_noise"):
            return cast(AgentState, state_any)

        _merge(state_any, plan_actions(cast(AgentState, state_any)))
        _merge(state_any, agent_class().run(state_any))
        _merge(state_any, diagnose(state_any))
        _merge(
            state_any,
            node_correlate_upstream(
                cast(AgentState, state_any),
                _build_correlation_config(state_any),
            ),
        )

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
