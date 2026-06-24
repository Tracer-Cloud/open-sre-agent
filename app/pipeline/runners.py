"""Public runner API — wraps the pipeline for CLI and external callers."""

from __future__ import annotations

import asyncio
import contextlib
import logging
import queue
import threading
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, cast

from app.remote.stream import StreamEvent
from app.state import AgentState, make_initial_state
from app.types.config import NodeConfig
from app.utils.errors import report_and_reraise
from app.utils.sentry_sdk import init_sentry

if TYPE_CHECKING:
    # Type-only — avoids paying the agent module's heavy import cost at
    # runner load while still letting static type-checkers validate
    # ``agent_class`` injections.
    from app.agent.stages.investigate import ConnectedInvestigationAgent

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
    from app.utils.sentry_sdk import capture_exception

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
        return _run(initial, agent_class=agent_class)


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
    from app.observability import silence_progress_tracker

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
            from app.agent.stages.diagnose import diagnose
            from app.agent.stages.extract_alert import extract_alert
            from app.agent.stages.investigate import ConnectedInvestigationAgent
            from app.agent.stages.plan_actions import plan_actions
            from app.agent.stages.publish_findings.node import generate_report
            from app.agent.stages.resolve_integrations import resolve_integrations
            from app.pipeline.pipeline import _merge

            state_any = cast(dict[str, Any], initial)

            # --- resolve_integrations ---
            _put(_make_node_event("on_chain_start", "resolve_integrations", {}))
            resolved = _traced_node("resolve_integrations", resolve_integrations, initial)
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
            _merge(state_any, _traced_node("extract_alert", extract_alert, initial))
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

            # --- plan_actions ---
            _put(_make_node_event("on_chain_start", "plan_actions", {}))
            _merge(
                state_any,
                _traced_node("plan_actions", plan_actions, cast("AgentState", state_any)),
            )
            _put(
                _make_node_event(
                    "on_chain_end",
                    "plan_actions",
                    {
                        "output": {
                            "planned_actions": state_any.get("planned_actions", []),
                            "plan_rationale": state_any.get("plan_rationale", ""),
                            "plan_audit": state_any.get("plan_audit", {}),
                        }
                    },
                )
            )

            # --- investigation agent (with real tool events) ---
            _merge(
                state_any,
                _traced_node(
                    "investigation_agent",
                    ConnectedInvestigationAgent().run,
                    state_any,
                    on_event=_on_agent_event,
                ),
            )

            # --- diagnose ---
            _put(_make_node_event("on_chain_start", "diagnose", {}))
            _merge(state_any, _traced_node("diagnose", diagnose, state_any))
            _put(
                _make_node_event(
                    "on_chain_end",
                    "diagnose",
                    {
                        "output": {
                            "root_cause": state_any.get("root_cause", ""),
                            "root_cause_category": state_any.get("root_cause_category", ""),
                            "validity_score": state_any.get("validity_score"),
                            "validated_claims": state_any.get("validated_claims", []),
                            "remediation_steps": state_any.get("remediation_steps", []),
                        }
                    },
                )
            )

            # --- upstream correlation ---
            from app.agent.correlation.node import node_correlate_upstream
            from app.pipeline.pipeline import _build_correlation_config

            _put(
                _make_node_event(
                    "on_chain_start",
                    "correlate_upstream",
                    {},
                )
            )

            _merge(
                state_any,
                _traced_node(
                    "correlate_upstream",
                    node_correlate_upstream,
                    cast("AgentState", state_any),
                    _build_correlation_config(state_any),
                ),
            )

            _put(
                _make_node_event(
                    "on_chain_end",
                    "correlate_upstream",
                    {
                        "output": {
                            "correlation": state_any.get("correlation", {}),
                        }
                    },
                )
            )

            # --- deliver / publish (skip terminal/editor render; StreamRenderer owns output) ---
            _put(_make_node_event("on_chain_start", "publish_findings", {}))
            _merge(
                state_any,
                _traced_node(
                    "publish_findings",
                    generate_report,
                    cast("Any", state_any),
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
