"""Raw-alert-first connected investigation pipeline."""

from __future__ import annotations

from typing import TYPE_CHECKING

from core.state import AgentState
from core.state.updates import apply_state_updates

if TYPE_CHECKING:
    # Type-only import — avoids paying the agent module's heavy import cost
    # at pipeline load while still letting static type-checkers validate
    # ``agent_class`` injections.
    from tools.investigation.stages.gather_evidence import ConnectedInvestigationAgent


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
    from platform.observability.sentry_sdk import capture_exception
    from tools.investigation.reporting import deliver
    from tools.investigation.stages.diagnose import diagnose
    from tools.investigation.stages.gather_evidence import get_investigation_agent_class
    from tools.investigation.stages.intake import extract_alert
    from tools.investigation.stages.plan_evidence import plan_actions
    from tools.investigation.stages.resolve_integrations import resolve_integrations

    agent_class = agent_class or get_investigation_agent_class()

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
