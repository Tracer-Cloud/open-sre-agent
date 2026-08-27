"""Agent-first investigation runner: host stages + native ``Agent`` gather loop.

This is the source of truth for investigation stage order. Host-side stages
(resolve, intake, plan, diagnose, deliver) stay pure ``(state) -> updates``
functions; evidence gathering runs through
``ConnectedInvestigationAgent`` / ``build_agent`` / ``core.agent.Agent``.

``tools.investigation.lifecycle.run_connected_investigation`` is a deprecated
facade over this module — keep it until callers migrate.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from enum import StrEnum
from typing import TYPE_CHECKING, Any

from core.state import AgentState
from core.state.updates import apply_state_updates
from infrastructure.analytics.investigation_loop import bind_investigation_loop_metrics_from_state

if TYPE_CHECKING:
    from tools.investigation.stages.gather_evidence import ConnectedInvestigationAgent

# Canonical host stage order (span names). Gather is the native ReAct agent.
INVESTIGATION_STAGE_ORDER: tuple[str, ...] = (
    "resolve_integrations",
    "intake",
    "plan_evidence",
    "gather_evidence",
    "diagnose",
    "deliver",
)

# Stream UI historically used different node names than span names.
_STREAM_NAME_BY_STAGE: Mapping[str, str] = {
    "resolve_integrations": "resolve_integrations",
    "intake": "extract_alert",
    "plan_evidence": "plan_actions",
    "gather_evidence": "investigation_agent",
    "diagnose": "diagnose",
    "correlate_upstream": "correlate_upstream",
    "deliver": "publish_findings",
}


class DeliverStyle(StrEnum):
    """How the final report stage runs."""

    FULL = "full"
    """``deliver()`` — optional eval + report + terminal/editor (sync CLI/API)."""

    STREAM = "stream"
    """Headless report for streaming UIs: correlate as its own step, then
    ``generate_report(render_terminal=False, open_editor=False)``.
    """


@dataclass(frozen=True)
class PipelineHooks:
    """Optional observers for streaming / telemetry around host stages.

    ``on_agent_event`` is forwarded to ``ConnectedInvestigationAgent.run`` so
    tool/LLM iterations surface as stream events. Stage start/end use the
    stream node names (e.g. ``extract_alert``), not internal span names.
    """

    on_stage_start: Callable[[str], None] | None = None
    on_stage_end: Callable[[str, Mapping[str, Any]], None] | None = None
    on_agent_event: Callable[..., Any] | None = None


@dataclass
class _StepRecorder:
    """Records completed host stages onto ``state['pipeline_steps']``."""

    steps: list[str] = field(default_factory=list)

    def mark(self, stage: str) -> None:
        self.steps.append(stage)

    def bind(self, state: AgentState) -> None:
        state["pipeline_steps"] = list(self.steps)


def _run_stage(
    span_name: str,
    stage: Callable[[AgentState], Any],
    state: AgentState,
    *,
    hooks: PipelineHooks | None,
    recorder: _StepRecorder,
    stream_name: str | None = None,
    end_output: Callable[[AgentState], Mapping[str, Any]] | None = None,
) -> None:
    """Merge one stage's updates under a stage span; optionally notify hooks."""
    from infrastructure.observability.trace.spans import stage_span

    stream = stream_name or _STREAM_NAME_BY_STAGE.get(span_name, span_name)
    if hooks and hooks.on_stage_start is not None:
        hooks.on_stage_start(stream)

    with stage_span(span_name):
        apply_state_updates(state, stage(state))

    recorder.mark(span_name)
    if hooks and hooks.on_stage_end is not None:
        output = end_output(state) if end_output is not None else {}
        hooks.on_stage_end(stream, output)


def run_agent_investigation(
    state: AgentState,
    *,
    agent_class: type[ConnectedInvestigationAgent] | None = None,
    hooks: PipelineHooks | None = None,
    deliver_style: DeliverStyle = DeliverStyle.FULL,
) -> AgentState:
    """Run resolve → intake → plan → native-agent gather → diagnose → deliver.

    Same stage functions as the legacy lifecycle graph; gather is the shared
    ``core.agent.Agent`` ReAct loop via ``ConnectedInvestigationAgent``.

    On success, ``state['pipeline_steps']`` lists completed host stage span
    names (noise short-circuit stops after ``intake``).
    """
    from infrastructure.observability.errors.sentry import capture_exception
    from tools.investigation.stages.diagnose import diagnose
    from tools.investigation.stages.gather_evidence import get_investigation_agent_class
    from tools.investigation.stages.intake import extract_alert
    from tools.investigation.stages.plan_evidence import plan_actions
    from tools.investigation.stages.resolve_integrations import resolve_integrations

    agent_class = agent_class or get_investigation_agent_class()
    recorder = _StepRecorder()
    hooks = hooks or PipelineHooks()

    try:
        _run_stage(
            "resolve_integrations",
            resolve_integrations,
            state,
            hooks=hooks,
            recorder=recorder,
            end_output=lambda s: {
                "resolved_integrations": _resolved_for_stream(s.get("resolved_integrations")),
            },
        )
        _run_stage(
            "intake",
            extract_alert,
            state,
            hooks=hooks,
            recorder=recorder,
            end_output=lambda s: {k: s.get(k) for k in ("alert_name", "severity")},
        )
        if state.get("is_noise"):
            recorder.bind(state)
            return state

        _run_stage(
            "plan_evidence",
            plan_actions,
            state,
            hooks=hooks,
            recorder=recorder,
            end_output=lambda s: {
                "planned_actions": s.get("planned_actions", []),
                "plan_rationale": s.get("plan_rationale", ""),
                "plan_audit": s.get("plan_audit", {}),
            },
        )

        _run_gather_stage(state, agent_class=agent_class, hooks=hooks, recorder=recorder)

        _run_stage(
            "diagnose",
            diagnose,
            state,
            hooks=hooks,
            recorder=recorder,
            end_output=lambda s: {
                "root_cause": s.get("root_cause", ""),
                "root_cause_category": s.get("root_cause_category", ""),
                "validity_score": s.get("validity_score"),
                "validated_claims": s.get("validated_claims", []),
                "remediation_steps": s.get("remediation_steps", []),
            },
        )

        _run_deliver_stages(state, hooks=hooks, recorder=recorder, deliver_style=deliver_style)
    except Exception as exc:
        bind_investigation_loop_metrics_from_state(state)
        recorder.bind(state)
        capture_exception(exc)
        raise

    recorder.bind(state)
    return state


def _run_gather_stage(
    state: AgentState,
    *,
    agent_class: type[ConnectedInvestigationAgent],
    hooks: PipelineHooks,
    recorder: _StepRecorder,
) -> None:
    """Run the native investigation agent under the gather_evidence span."""
    from infrastructure.observability.trace.spans import stage_span

    # Stream UIs get start/end from the agent's own on_event callbacks
    # (agent_start / agent_end); do not emit a second host chain for gather.
    with stage_span("gather_evidence"):
        if hooks.on_agent_event is not None:
            apply_state_updates(
                state,
                agent_class().run(state, on_event=hooks.on_agent_event),
            )
        else:
            apply_state_updates(state, agent_class().run(state))
    recorder.mark("gather_evidence")


def _run_deliver_stages(
    state: AgentState,
    *,
    hooks: PipelineHooks,
    recorder: _StepRecorder,
    deliver_style: DeliverStyle,
) -> None:
    if deliver_style is DeliverStyle.STREAM:
        from tools.investigation.reporting.node import generate_report
        from tools.investigation.reporting.upstream_correlation import (
            enrich_upstream_correlation,
        )

        _run_stage(
            "correlate_upstream",
            enrich_upstream_correlation,
            state,
            hooks=hooks,
            recorder=recorder,
            stream_name="correlate_upstream",
            end_output=lambda s: {"correlation": s.get("correlation", {})},
        )

        def _publish(s: AgentState) -> dict[str, Any]:
            return generate_report(s, render_terminal=False, open_editor=False)

        _run_stage(
            "deliver",
            _publish,
            state,
            hooks=hooks,
            recorder=recorder,
            stream_name="publish_findings",
            end_output=lambda s: {
                "root_cause": s.get("root_cause", ""),
                "root_cause_category": s.get("root_cause_category", ""),
                "validity_score": s.get("validity_score"),
                "report": s.get("report", ""),
                "slack_message": s.get("slack_message", ""),
                "problem_md": s.get("problem_md", ""),
                "validated_claims": s.get("validated_claims", []),
                "remediation_steps": s.get("remediation_steps", []),
            },
        )
        return

    from tools.investigation.reporting import deliver

    _run_stage(
        "deliver",
        deliver,
        state,
        hooks=hooks,
        recorder=recorder,
        end_output=lambda s: {
            "root_cause": s.get("root_cause", ""),
            "slack_message": s.get("slack_message", ""),
            "problem_md": s.get("problem_md", ""),
        },
    )


def _resolved_for_stream(resolved: Any) -> Any:
    """Serialize resolved integrations for stream payloads when available."""
    if not isinstance(resolved, dict):
        return {}
    try:
        from tools.investigation.streaming import resolved_integrations_stream_payload

        return resolved_integrations_stream_payload(resolved)
    except Exception:
        return resolved


def completed_pipeline_steps(state: Mapping[str, Any]) -> Sequence[str]:
    """Return recorded host stages from a finished investigation state."""
    raw = state.get("pipeline_steps")
    if isinstance(raw, list):
        return [str(item) for item in raw]
    return ()
