"""Public runner API — wraps the pipeline for CLI and external callers."""

from __future__ import annotations

import asyncio
import contextlib
import contextvars
import logging
import queue
import threading
from collections.abc import AsyncIterator, Callable, Mapping
from typing import TYPE_CHECKING, Any, cast

from config.constants.investigation import MAX_INVESTIGATION_LOOPS
from core.domain.stream import StreamEvent
from core.state import AgentState
from infrastructure.observability.errors.boundary import report_and_reraise
from infrastructure.observability.errors.sentry import init_sentry
from infrastructure.observability.trace.spans import stage_span
from tools.investigation.state_factory import make_initial_state
from tools.investigation.streaming import InvestigationPipelineStreamError

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
    from infrastructure.observability.errors.sentry import capture_exception

    capture_exception(exc, context=context, tags=tags)
    _mark_exception_captured(exc)


def _loop_metrics_for_error(state: Mapping[str, Any] | None) -> tuple[int, int]:
    """Return ``(loop_count, iteration_cap)`` for error delivery; never raises."""
    try:
        from infrastructure.analytics.investigation_loop import loop_metrics_from_state

        return loop_metrics_from_state(state)
    except Exception:
        return 0, MAX_INVESTIGATION_LOOPS


def _traced_node(node_name: str, fn: Callable[..., Any], *args: Any, **kwargs: Any) -> Any:
    with stage_span(node_name):
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
    investigation_metadata: tuple[str, str] | None = None,
    agent_class: type[ConnectedInvestigationAgent] | None = None,
) -> AgentState:
    """Run the investigation from a raw alert payload. Pure function: inputs in, state out.

    Args:
        raw_alert: The original alert payload or free-text alert description.
        resolved_integrations: Optional pre-resolved integrations dict. When provided,
            integration resolution is skipped — useful for synthetic testing where a
            FixtureGrafanaBackend should be injected without real credential resolution.
        investigation_metadata: Optional ``(alert_name, severity)`` for AgentState.
        agent_class: Optional override for the investigation agent class. Defaults
            to ``ConnectedInvestigationAgent``. Callers that need a custom
            termination policy, structured-stage progression, or other
            agent-level extensions can pass a subclass instead.
    """
    init_sentry(entrypoint="pipeline")
    from tools.investigation.agent_pipeline import run_agent_investigation as _run

    initial = make_initial_state(
        raw_alert=raw_alert,
        opensre_evaluate=opensre_evaluate,
        investigation_metadata=investigation_metadata,
    )
    if resolved_integrations is not None:
        cast(dict[str, Any], initial)["resolved_integrations"] = resolved_integrations
    if openclaw_context:
        from core.state.channel_context import set_channel_context

        set_channel_context(cast(dict[str, Any], initial), "openclaw", openclaw_context)

    with report_and_reraise(
        logger=logger,
        message="run_investigation failed",
        tags={"surface": "pipeline", "component": "tools.investigation.capability"},
    ):
        from infrastructure.analytics.investigation_loop import (
            bind_investigation_loop_metrics_from_state,
        )

        state = _run(initial, agent_class=agent_class)
        bind_investigation_loop_metrics_from_state(state)
        return state


def resolve_investigation_context(
    *,
    raw_alert: dict[str, Any],
    alert_name: str | None,
    severity: str | None,
) -> tuple[str, str]:
    """Resolve ``(alert_name, severity)`` from overrides and payload defaults.

    Pure helper shared by every delivery surface (CLI, HTTP server, MCP);
    overrides win, then the raw alert's own fields, then common labels.
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
    investigation_metadata: tuple[str, str] | None = None,
) -> dict[str, Any]:
    """Run an investigation and return the serializable result payload.

    The headless counterpart used by surfaces that do not render a live terminal
    stream (HTTP server, MCP, integration webhooks). It returns the same ``dict`` the
    CLI produces without depending on the ``cli`` package, so callers no longer have
    to reach up into ``cli.investigation`` to run an investigation.

    ``investigation_metadata`` is an optional ``(alert_name, severity)`` tuple.
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
    investigation_metadata: tuple[str, str] | None = None,
    user_requested: bool = False,
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
        user_requested=user_requested,
    )

    # Silence the global ProgressTracker before starting the background thread
    # so pipeline internals (extract_alert, resolve_integrations, etc.) don't
    # open their own Rich Live display — the StreamRenderer drives it instead.
    from infrastructure.observability import silence_progress_tracker

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
        state = initial
        try:
            from tools.investigation.agent_pipeline import (
                DeliverStyle,
                PipelineHooks,
                run_agent_investigation,
            )

            def _on_stage_start(stream_name: str) -> None:
                _put(_make_node_event("on_chain_start", stream_name, {}))

            def _on_stage_end(stream_name: str, output: Mapping[str, Any]) -> None:
                _put(
                    _make_node_event(
                        "on_chain_end",
                        stream_name,
                        {"output": dict(output)},
                    )
                )

            run_agent_investigation(
                state,
                hooks=PipelineHooks(
                    on_stage_start=_on_stage_start,
                    on_stage_end=_on_stage_end,
                    on_agent_event=_on_agent_event,
                ),
                deliver_style=DeliverStyle.STREAM,
            )

        except Exception as exc:
            loop_count, iteration_cap = _loop_metrics_for_error(state)
            _capture_exception_once(exc, context="pipeline.astream_investigation")
            with contextlib.suppress(RuntimeError):
                loop.call_soon_threadsafe(
                    event_queue.put_nowait,
                    InvestigationPipelineStreamError(
                        cause=exc,
                        loop_count=loop_count,
                        iteration_cap=iteration_cap,
                    ),
                )
        finally:
            with contextlib.suppress(RuntimeError):
                loop.call_soon_threadsafe(event_queue.put_nowait, None)

    # Copy the caller's context so ContextVar bindings (session trace) reach the thread.
    thread = threading.Thread(
        target=contextvars.copy_context().run, args=(_run_pipeline,), daemon=True
    )
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
        if isinstance(item, InvestigationPipelineStreamError):
            raise item
        if isinstance(item, BaseException):
            raise item
        yield item

    thread.join()
