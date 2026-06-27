"""Public runner API — wraps the pipeline for CLI and external callers."""

from __future__ import annotations

import asyncio
import contextlib
import logging
import queue
import threading
from collections.abc import AsyncIterator, Callable
from typing import TYPE_CHECKING, Any, cast

from context.state import AgentState
from core.domain.stream import StreamEvent
from platform.observability.errors import report_and_reraise
from platform.observability.sentry_sdk import init_sentry
from tools.investigation.state_factory import make_initial_state
from tools.investigation.streaming import resolved_integrations_stream_payload

if TYPE_CHECKING:
    # Type-only — avoids paying the agent module's heavy import cost at
    # runner load while still letting static type-checkers validate
    # ``agent_class`` injections.
    from tools.investigation.stages.gather_evidence import ConnectedInvestigationAgent

logger = logging.getLogger(__name__)

_SENTRY_CAPTURED_ATTR = "_opensre_sentry_captured"


def _exception_was_captured(exc: BaseException) -> bool:
    return bool(getattr(exc, _SENTRY_CAPTURED_ATTR, False))


def _mark_exception_captured(exc: BaseException) -> None:
    with contextlib.suppress(Exception):
        setattr(exc, _SENTRY_CAPTURED_ATTR, True)


def _capture_exception_once(
    exc: BaseException,
    *,
    context: str,
    tags: dict[str, str] | None = None,
) -> None:
    if _exception_was_captured(exc):
        return
    from platform.observability.sentry_sdk import capture_exception

    capture_exception(exc, context=context, tags=tags)
    _mark_exception_captured(exc)


def _traced_node(node_name: str, fn: Callable[..., Any], *args: Any, **kwargs: Any) -> Any:
    try:
        return fn(*args, **kwargs)
    except Exception as exc:
        _capture_exception_once(
            exc,
            context=f"node.{node_name}",
            tags={"surface": "node", "node": node_name},
        )
        raise


def run_investigation(
    raw_alert: str | dict[str, Any],
    *,
    resolved_integrations: dict[str, Any] | None = None,
    openclaw_context: dict[str, Any] | None = None,
    opensre_evaluate: bool = False,
    investigation_metadata: tuple[str, str, str] | None = None,
    agent_class: type[ConnectedInvestigationAgent] | None = None,
) -> AgentState:
    """Run the investigation from a raw alert payload. Pure function: inputs in, state out.

    Args:
        raw_alert: The original alert payload or free-text alert description.
        resolved_integrations: Optional pre-resolved integrations dict. When provided,
            integration resolution is skipped — useful for synthetic testing where a
            FixtureGrafanaBackend should be injected without real credential resolution.
        investigation_metadata: Optional ``(alert_name, pipeline_name, severity)`` for
            initial state; avoids copying those fields onto ``raw_alert``.
        agent_class: Optional override for the investigation agent class. Defaults
            to ``ConnectedInvestigationAgent``. Callers that need a custom
            termination policy, structured-stage progression, or other
            agent-level extensions can pass a subclass instead.
    """
    init_sentry(entrypoint="pipeline")
    from tools.investigation.lifecycle import run_connected_investigation as _run

    initial = make_initial_state(
        raw_alert=raw_alert,
        opensre_evaluate=opensre_evaluate,
        investigation_metadata=investigation_metadata,
    )
    if resolved_integrations is not None:
        cast(dict[str, Any], initial)["resolved_integrations"] = resolved_integrations
    if openclaw_context:
        cast(dict[str, Any], initial)["openclaw_context"] = dict(openclaw_context)

    with report_and_reraise(
        logger=logger,
        message="run_investigation failed",
        tags={"surface": "pipeline", "component": "tools.investigation.capability"},
    ):
        return _run(initial, agent_class=agent_class)


def resolve_investigation_context(
    *,
    raw_alert: dict[str, Any],
    alert_name: str | None,
    pipeline_name: str | None,
    severity: str | None,
) -> tuple[str, str, str]:
    """Resolve ``(alert_name, pipeline_name, severity)`` from overrides and payload defaults.

    Pure helper shared by every delivery surface (CLI, HTTP server, MCP); overrides win,
    then the raw alert's own fields, then common labels, then sensible fallbacks.
    """
    labels = raw_alert.get("commonLabels") or raw_alert.get("labels") or {}
    labels = labels if isinstance(labels, dict) else {}
    canonical = raw_alert.get("canonical_alert")
    canonical = canonical if isinstance(canonical, dict) else {}
    return (
        alert_name
        or raw_alert.get("alert_name")
        or raw_alert.get("title")
        or canonical.get("alert_name")
        or labels.get("alertname")
        or "Incident",
        pipeline_name
        or raw_alert.get("pipeline_name")
        or canonical.get("pipeline_name")
        or labels.get("pipeline_name")
        or labels.get("pipeline")
        or labels.get("service")
        or "unknown",
        severity
        or raw_alert.get("severity")
        or canonical.get("severity")
        or labels.get("severity")
        or "warning",
    )


def build_investigation_payload(
    state: AgentState,
    *,
    opensre_evaluate: bool = False,
) -> dict[str, Any]:
    """Project a finished investigation ``AgentState`` into the public result payload.

    Shared by every delivery surface so the serializable result shape stays identical
    regardless of how the run was triggered (CLI, HTTP server, MCP, integration webhook).
    """
    out: dict[str, Any] = {
        "report": state["slack_message"],
        "problem_md": state["problem_md"],
        "root_cause": state["root_cause"],
        "is_noise": state.get("is_noise", False),
        "validity_score": state.get("validity_score", 0.0),
    }
    if state.get("evidence_entries"):
        out["tool_calls"] = state["evidence_entries"]
    if opensre_evaluate:
        ev = state.get("opensre_llm_eval")
        if isinstance(ev, dict) and ev:
            out["opensre_llm_eval"] = ev
        elif not (state.get("opensre_eval_rubric") or "").strip():
            out["opensre_llm_eval"] = {
                "skipped": True,
                "reason": (
                    "No scoring_points on this alert — nothing to judge against "
                    "(not a scoring_points rubric payload, or field missing)."
                ),
            }
        else:
            out["opensre_llm_eval"] = {
                "skipped": True,
                "reason": "Evaluate was enabled but no judge output was recorded.",
            }
    return out


def run_investigation_payload(
    *,
    raw_alert: str | dict[str, Any],
    opensre_evaluate: bool = False,
    investigation_metadata: tuple[str, str, str] | None = None,
) -> dict[str, Any]:
    """Run an investigation and return the serializable result payload.

    The headless counterpart used by surfaces that do not render a live terminal
    stream (HTTP server, MCP, integration webhooks). It returns the same ``dict`` the
    CLI produces without depending on the ``cli`` package, so callers no longer have
    to reach up into ``cli.investigation`` to run an investigation.

    ``investigation_metadata`` is an optional ``(alert_name, pipeline_name, severity)``
    tuple for initial state (e.g. HTTP request overrides) without mutating ``raw_alert``.
    """
    state = run_investigation(
        raw_alert,
        opensre_evaluate=opensre_evaluate,
        investigation_metadata=investigation_metadata,
    )
    return build_investigation_payload(state, opensre_evaluate=opensre_evaluate)


async def astream_investigation(
    raw_alert: str | dict[str, Any],
    *,
    opensre_evaluate: bool = False,
    investigation_metadata: tuple[str, str, str] | None = None,
) -> AsyncIterator[Any]:
    """Stream investigation events in real time.

    Runs the pipeline in a background thread and yields StreamEvents as each
    stage and tool call happens. The renderer sees individual tool_start /
    tool_end events and shows them as spinner subtext, just like Claude Code.
    """
    init_sentry(entrypoint="pipeline")

    initial = make_initial_state(
        raw_alert=raw_alert,
        opensre_evaluate=opensre_evaluate,
        investigation_metadata=investigation_metadata,
    )

    # Silence the global ProgressTracker before starting the background thread
    # so pipeline internals (extract_alert, resolve_integrations, etc.) don't
    # open their own Rich Live display — the StreamRenderer drives it instead.
    from platform.observability import silence_progress_tracker

    silence_progress_tracker()

    event_queue: queue.Queue[StreamEvent | BaseException | None] = queue.Queue()
    loop = asyncio.get_running_loop()

    def _put(evt: StreamEvent) -> None:
        with contextlib.suppress(RuntimeError):  # loop already closed; consumer is gone
            loop.call_soon_threadsafe(event_queue.put_nowait, evt)

    def _make_node_event(kind: str, node: str, data: dict[str, Any]) -> StreamEvent:
        return StreamEvent(
            event_type="events",
            data={"event": kind, "name": node, "data": data},
            node_name=node,
            kind=kind,
            run_id="",
            tags=["graph:step:0"],
        )

    def _make_tool_event(kind: str, name: str, data: dict[str, Any]) -> StreamEvent:
        # Tool events carry the name in data so the renderer can extract it.
        payload = dict(data)
        payload["name"] = name
        payload["event"] = kind
        return StreamEvent(
            event_type="events",
            data=payload,
            node_name="investigation_agent",
            kind=kind,
            run_id="",
            tags=[],
        )

    def _on_agent_event(event_kind: str, data: dict[str, Any]) -> None:
        if event_kind == "agent_start":
            _put(_make_node_event("on_chain_start", "investigation_agent", data))
        elif event_kind == "tool_start":
            _put(_make_tool_event("on_tool_start", data.get("name", "tool"), data))
        elif event_kind == "tool_end":
            _put(_make_tool_event("on_tool_end", data.get("name", "tool"), data))
        elif event_kind == "llm_start":
            # Forward LLM iterations so the renderer can print "analyzing…" hints
            # during the silent gap between tool batches and during synthesis.
            _put(_make_tool_event("on_llm_start", "investigation_agent", data))
        elif event_kind == "agent_end":
            _put(
                _make_node_event(
                    "on_chain_end",
                    "investigation_agent",
                    {"output": data},
                )
            )

    def _run_pipeline() -> None:
        try:
            from context.state.updates import apply_state_updates
            from tools.investigation.reporting.node import generate_report
            from tools.investigation.stages.diagnose import diagnose
            from tools.investigation.stages.gather_evidence import ConnectedInvestigationAgent
            from tools.investigation.stages.intake import extract_alert
            from tools.investigation.stages.plan_evidence import plan_actions
            from tools.investigation.stages.resolve_integrations import resolve_integrations

            state = initial

            # --- resolve_integrations ---
            _put(_make_node_event("on_chain_start", "resolve_integrations", {}))
            resolved_updates = _traced_node("resolve_integrations", resolve_integrations, state)
            apply_state_updates(state, resolved_updates)
            resolved = resolved_updates.get("resolved_integrations") or {}
            _put(
                _make_node_event(
                    "on_chain_end",
                    "resolve_integrations",
                    {
                        "output": {
                            "resolved_integrations": resolved_integrations_stream_payload(resolved)
                        }
                    },
                )
            )

            # --- extract_alert ---
            _put(_make_node_event("on_chain_start", "extract_alert", {}))
            apply_state_updates(state, _traced_node("extract_alert", extract_alert, state))
            _put(
                _make_node_event(
                    "on_chain_end",
                    "extract_alert",
                    {
                        "output": {
                            k: state.get(k) for k in ("alert_name", "pipeline_name", "severity")
                        }
                    },
                )
            )

            if state.get("is_noise"):
                with contextlib.suppress(RuntimeError):  # loop closed (consumer cancelled)
                    loop.call_soon_threadsafe(event_queue.put_nowait, None)
                return

            # --- plan_actions ---
            _put(_make_node_event("on_chain_start", "plan_actions", {}))
            apply_state_updates(
                state,
                _traced_node("plan_actions", plan_actions, state),
            )
            _put(
                _make_node_event(
                    "on_chain_end",
                    "plan_actions",
                    {
                        "output": {
                            "planned_actions": state.get("planned_actions", []),
                            "plan_rationale": state.get("plan_rationale", ""),
                            "plan_audit": state.get("plan_audit", {}),
                        }
                    },
                )
            )

            # --- investigation agent (with real tool events) ---
            apply_state_updates(
                state,
                _traced_node(
                    "investigation_agent",
                    ConnectedInvestigationAgent().run,
                    state,
                    on_event=_on_agent_event,
                ),
            )

            # --- diagnose ---
            _put(_make_node_event("on_chain_start", "diagnose", {}))
            apply_state_updates(state, _traced_node("diagnose", diagnose, state))
            _put(
                _make_node_event(
                    "on_chain_end",
                    "diagnose",
                    {
                        "output": {
                            "root_cause": state.get("root_cause", ""),
                            "root_cause_category": state.get("root_cause_category", ""),
                            "validity_score": state.get("validity_score"),
                            "validated_claims": state.get("validated_claims", []),
                            "remediation_steps": state.get("remediation_steps", []),
                        }
                    },
                )
            )

            # --- upstream correlation ---
            from tools.investigation.reporting.upstream_correlation import (
                enrich_upstream_correlation,
            )

            _put(
                _make_node_event(
                    "on_chain_start",
                    "correlate_upstream",
                    {},
                )
            )

            apply_state_updates(
                state,
                _traced_node(
                    "correlate_upstream",
                    enrich_upstream_correlation,
                    state,
                ),
            )

            _put(
                _make_node_event(
                    "on_chain_end",
                    "correlate_upstream",
                    {
                        "output": {
                            "correlation": state.get("correlation", {}),
                        }
                    },
                )
            )

            # --- deliver / publish (skip terminal/editor render; StreamRenderer owns output) ---
            _put(_make_node_event("on_chain_start", "publish_findings", {}))
            apply_state_updates(
                state,
                _traced_node(
                    "publish_findings",
                    generate_report,
                    state,
                    render_terminal=False,
                    open_editor=False,
                ),
            )

            _put(
                _make_node_event(
                    "on_chain_end",
                    "publish_findings",
                    {
                        "output": {
                            "root_cause": state.get("root_cause", ""),
                            "root_cause_category": state.get("root_cause_category", ""),
                            "validity_score": state.get("validity_score"),
                            "report": state.get("report", ""),
                            "slack_message": state.get("slack_message", ""),
                            "problem_md": state.get("problem_md", ""),
                            "validated_claims": state.get("validated_claims", []),
                            "remediation_steps": state.get("remediation_steps", []),
                        }
                    },
                )
            )

        except Exception as exc:
            _capture_exception_once(exc, context="pipeline.astream_investigation")
            with contextlib.suppress(RuntimeError):
                loop.call_soon_threadsafe(event_queue.put_nowait, exc)
        finally:
            with contextlib.suppress(RuntimeError):
                loop.call_soon_threadsafe(event_queue.put_nowait, None)

    thread = threading.Thread(target=_run_pipeline, daemon=True)
    thread.start()

    while True:
        # Drain the queue without blocking the event loop
        try:
            item = event_queue.get_nowait()
        except queue.Empty:
            await asyncio.sleep(0.01)
            continue

        if item is None:
            break
        if isinstance(item, BaseException):
            raise item
        yield item

    thread.join()
