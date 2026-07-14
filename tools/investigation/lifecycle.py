"""Raw-alert-first connected investigation pipeline."""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING, Any

from core.state import AgentState
from core.state.updates import apply_state_updates
from platform.analytics.investigation_loop import bind_investigation_loop_metrics_from_state

if TYPE_CHECKING:
    # Type-only import — avoids paying the agent module's heavy import cost
    # at pipeline load while still letting static type-checkers validate
    # ``agent_class`` injections.
    from tools.investigation.stages.gather_evidence import ConnectedInvestigationAgent


def _run_stage(name: str, stage: Callable[[AgentState], Any], state: AgentState) -> None:
    """Merge one pipeline stage's updates into ``state`` under a stage trace span."""
    from platform.observability.trace.spans import stage_span

    with stage_span(name):
        apply_state_updates(state, stage(state))


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
    from platform.observability.errors.sentry import capture_exception
    from tools.investigation.reporting import deliver
    from tools.investigation.stages.diagnose import diagnose
    from tools.investigation.stages.gather_evidence import get_investigation_agent_class
    from tools.investigation.stages.intake import extract_alert
    from tools.investigation.stages.plan_evidence import plan_actions
    from tools.investigation.stages.resolve_integrations import resolve_integrations

    agent_class = agent_class or get_investigation_agent_class()

    try:
        _run_stage("resolve_integrations", resolve_integrations, state)
        _run_stage("intake", extract_alert, state)
        if state.get("is_noise"):
            return state

        _run_stage("plan_evidence", plan_actions, state)
        _run_stage("gather_evidence", agent_class().run, state)
        _run_stage("diagnose", diagnose, state)
        _run_stage("deliver", deliver, state)
    except Exception as exc:
        bind_investigation_loop_metrics_from_state(state)
        capture_exception(exc)
        raise

    return state
