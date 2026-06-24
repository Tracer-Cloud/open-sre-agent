"""Raw-alert-first connected investigation pipeline."""

from __future__ import annotations

from typing import TYPE_CHECKING

from app.core.orchestration.state_updates import apply_state_updates
from app.state import AgentState

if TYPE_CHECKING:
    # Type-only import — avoids paying the agent module's heavy import cost
    # at pipeline load while still letting static type-checkers validate
    # ``agent_class`` injections.
    from app.core.orchestration.node.investigate import ConnectedInvestigationAgent


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
    from app.core.orchestration.node.diagnose import diagnose
    from app.core.orchestration.node.extract_alert import extract_alert
    from app.core.orchestration.node.investigate import ConnectedInvestigationAgent
    from app.core.orchestration.node.plan_actions import plan_actions
    from app.core.orchestration.node.publish_findings import deliver
    from app.core.orchestration.node.resolve_integrations import resolve_integrations
    from app.utils.sentry_sdk import capture_exception

    agent_class = agent_class or ConnectedInvestigationAgent

    try:
        apply_state_updates(state, resolve_integrations(state))
        apply_state_updates(state, extract_alert(state))
        if state.get("is_noise"):
            return state

        apply_state_updates(state, plan_actions(state))
        apply_state_updates(state, agent_class().run(state))
        apply_state_updates(state, diagnose(state))
        apply_state_updates(state, deliver(state))
    except Exception as exc:
        capture_exception(exc)
        raise

    return state
