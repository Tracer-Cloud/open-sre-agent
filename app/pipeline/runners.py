"""Standalone runners for testing and CLI — run the pipeline without LangGraph."""

from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Any, cast

from app.nodes.chat import chat_agent_node, general_node, router_node
from app.remote.stream import StreamEvent
from app.state import AgentState, make_initial_state
from app.types.config import NodeConfig
from app.utils.sentry_sdk import capture_exception, init_sentry


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


def run_chat(state: AgentState, config: NodeConfig | None = None) -> AgentState:
    """Run chat routing + response without LangGraph (for testing)."""
    init_sentry()
    cfg = config or {"configurable": {}}
    try:
        _merge_state(state, router_node(state))
        if state.get("route") == "tracer_data":
            _merge_state(state, chat_agent_node(state, cfg))
        else:
            _merge_state(state, general_node(state, cfg))
    except Exception as exc:
        capture_exception(exc)
        raise
    return state


def run_investigation(
    alert_name: str,
    pipeline_name: str,
    severity: str,
    raw_alert: str | dict[str, Any] | None = None,
    resolved_integrations: dict[str, Any] | None = None,
    *,
    opensre_evaluate: bool = False,
) -> AgentState:
    """Run investigation pipeline via LangGraph. Pure function: inputs in, state out.

    Args:
        resolved_integrations: Optional pre-resolved integrations dict. When provided,
            node_resolve_integrations is skipped — useful for synthetic testing where a
            FixtureGrafanaBackend should be injected without real credential resolution.
    """
    init_sentry()
    from app.pipeline.graph import graph as compiled_graph  # lazy to avoid circular import

    initial = make_initial_state(
        alert_name,
        pipeline_name,
        severity,
        raw_alert=raw_alert,
        opensre_evaluate=opensre_evaluate,
    )
    if resolved_integrations is not None:
        cast(dict[str, Any], initial)["resolved_integrations"] = resolved_integrations
    try:
        return cast(AgentState, compiled_graph.invoke(initial))
    except Exception as exc:
        capture_exception(exc)
        raise


async def astream_investigation(
    alert_name: str,
    pipeline_name: str,
    severity: str,
    raw_alert: str | dict[str, Any] | None = None,
    *,
    opensre_evaluate: bool = False,
) -> AsyncIterator[StreamEvent]:
    """Stream investigation events via LangGraph's ``astream_events``.

    Yields :class:`StreamEvent` objects compatible with the remote
    ``StreamRenderer``, so local and remote investigations share the
    same terminal UX.
    """
    init_sentry()
    from app.pipeline.graph import graph as compiled_graph  # lazy to avoid circular import

    initial = make_initial_state(
        alert_name,
        pipeline_name,
        severity,
        raw_alert=raw_alert,
        opensre_evaluate=opensre_evaluate,
    )

    try:
        async for event in compiled_graph.astream_events(initial, version="v2"):
            yield _map_langgraph_event(dict(event))
    except Exception as exc:
        capture_exception(exc)
        raise


def _map_langgraph_event(event: dict[str, Any]) -> StreamEvent:
    """Convert a raw LangGraph ``astream_events`` dict to a ``StreamEvent``."""
    kind = event.get("event", "")
    name = event.get("name", "")
    metadata = event.get("metadata", {})
    node_name = metadata.get("langgraph_node", "") if isinstance(metadata, dict) else ""
    tags = event.get("tags", [])
    run_id = event.get("run_id", "")
    data = {
        "event": kind,
        "name": name,
        "data": event.get("data", {}),
        "metadata": metadata,
    }

    return StreamEvent(
        event_type="events",
        data=data,
        node_name=node_name or name,
        kind=kind,
        run_id=run_id,
        tags=list(tags) if isinstance(tags, list) else [],
    )


@dataclass
class SimpleAgent:
    def invoke(self, state: AgentState, config: NodeConfig | None = None) -> AgentState:
        init_sentry()
        from app.pipeline.graph import graph as compiled_graph  # lazy to avoid circular import

        cfg = config or {"configurable": {}}
        try:
            return cast(AgentState, compiled_graph.invoke(state, cast(Any, cfg)))
        except Exception as exc:
            capture_exception(exc)
            raise
