"""Public runner API — wraps the pipeline for CLI and external callers."""

from __future__ import annotations

import asyncio
import contextlib
import logging
import queue
import threading
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Any, cast

from app.remote.stream import StreamEvent
from app.state import AgentState, make_initial_state
from app.types.config import NodeConfig
from app.utils.errors import report_and_reraise
from app.utils.sentry_sdk import init_sentry

logger = logging.getLogger(__name__)

# Serializes temporary render_report monkeypatches when multiple streaming
# investigations run concurrently (e.g. remote server).
_render_report_patch_lock = threading.Lock()


def _merge_state(state: AgentState, updates: dict[str, Any]) -> None:
    if not updates:
        return
    state_any = cast(dict[str, Any], state)
    for key, value in updates.items():
        if key == "messages":
            messages = list(state_any.get("messages", []))
            messages.extend(value) if isinstance(value, list) else messages.append(value)
            state_any["messages"] = messages
            continue
        state_any[key] = value


def run_investigation(
    raw_alert: str | dict[str, Any],
    *,
    resolved_integrations: dict[str, Any] | None = None,
    openclaw_context: dict[str, Any] | None = None,
    opensre_evaluate: bool = False,
    investigation_metadata: tuple[str, str, str] | None = None,
) -> AgentState:
    """Run the investigation from a raw alert payload. Pure function: inputs in, state out.

    Args:
        raw_alert: The original alert payload or free-text alert description.
        resolved_integrations: Optional pre-resolved integrations dict. When provided,
            integration resolution is skipped — useful for synthetic testing where a
            FixtureGrafanaBackend should be injected without real credential resolution.
        investigation_metadata: Optional ``(alert_name, pipeline_name, severity)`` for
            initial state; avoids copying those fields onto ``raw_alert``.
    """
    init_sentry(entrypoint="pipeline")
    from app.pipeline.pipeline import run_connected_investigation as _run

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
        tags={"surface": "pipeline", "component": "app.pipeline.runners"},
    ):
        return _run(initial)


def run_chat(state: AgentState, _config: NodeConfig | None = None) -> AgentState:
    """Run chat routing + response (for testing/CLI use)."""
    init_sentry(entrypoint="pipeline")
    from app.pipeline.pipeline import run_chat as _run

    with report_and_reraise(
        logger=logger,
        message="run_chat failed",
        tags={"surface": "pipeline", "component": "app.pipeline.runners"},
    ):
        return _run(state)


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
    from app.cli.support.output import set_silent_tracker

    set_silent_tracker()

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
            from app.agent.context import resolve_integrations
            from app.agent.extract import extract_alert
            from app.agent.investigation import ConnectedInvestigationAgent
            from app.delivery.publish_findings.node import generate_report
            from app.pipeline.pipeline import _merge

            state_any = cast(dict[str, Any], initial)

            # --- resolve_integrations ---
            _put(_make_node_event("on_chain_start", "resolve_integrations", {}))
            resolved = resolve_integrations(initial)
            _merge(state_any, {"resolved_integrations": resolved})
            _put(
                _make_node_event(
                    "on_chain_end",
                    "resolve_integrations",
                    {
                        "output": {
                            "resolved_integrations": {
                                k: v for k, v in resolved.items() if k != "_all"
                            }
                        }
                    },
                )
            )

            # --- extract_alert ---
            _put(_make_node_event("on_chain_start", "extract_alert", {}))
            _merge(state_any, extract_alert(initial))
            _put(
                _make_node_event(
                    "on_chain_end",
                    "extract_alert",
                    {
                        "output": {
                            k: state_any.get(k) for k in ("alert_name", "pipeline_name", "severity")
                        }
                    },
                )
            )

            if state_any.get("is_noise"):
                with contextlib.suppress(RuntimeError):  # loop closed (consumer cancelled)
                    loop.call_soon_threadsafe(event_queue.put_nowait, None)
                return

            # --- investigation agent (with real tool events) ---
            _merge(
                state_any, ConnectedInvestigationAgent().run(state_any, on_event=_on_agent_event)
            )

            # --- deliver / publish (skip terminal render — StreamRenderer owns it) ---
            _put(_make_node_event("on_chain_start", "publish_findings", {}))

            # Patch render_report to a no-op so generate_report handles external
            # delivery but leaves terminal rendering to the StreamRenderer.
            from app.delivery.publish_findings import node as _publish_node
            from app.delivery.publish_findings.renderers import terminal as _term_mod

            with _render_report_patch_lock:
                _orig_terminal_render = _term_mod.render_report
                _orig_node_render = _publish_node.render_report
                _term_mod.render_report = lambda *_a, **_kw: None  # type: ignore[assignment]
                _publish_node.render_report = lambda *_a, **_kw: None  # type: ignore[assignment]
                try:
                    _merge(state_any, generate_report(cast("Any", state_any)))
                finally:
                    _term_mod.render_report = _orig_terminal_render  # type: ignore[assignment]
                    _publish_node.render_report = _orig_node_render  # type: ignore[assignment]

            _put(
                _make_node_event(
                    "on_chain_end",
                    "publish_findings",
                    {
                        "output": {
                            "root_cause": state_any.get("root_cause", ""),
                            "root_cause_category": state_any.get("root_cause_category", ""),
                            "validity_score": state_any.get("validity_score"),
                            "report": state_any.get("report", ""),
                            "slack_message": state_any.get("slack_message", ""),
                            "problem_md": state_any.get("problem_md", ""),
                            "validated_claims": state_any.get("validated_claims", []),
                            "remediation_steps": state_any.get("remediation_steps", []),
                        }
                    },
                )
            )

        except Exception as exc:
            from app.utils.sentry_sdk import capture_exception

            capture_exception(exc)
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


@dataclass
class SimpleAgent:
    def invoke(self, state: AgentState, _config: NodeConfig | None = None) -> AgentState:
        init_sentry(entrypoint="pipeline")
        from app.pipeline.pipeline import run_connected_investigation as _run

        with report_and_reraise(
            logger=logger,
            message="SimpleAgent.invoke failed",
            tags={"surface": "pipeline", "component": "app.pipeline.runners"},
        ):
            return _run(state)
