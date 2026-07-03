"""Unit tests for the AgentEventEmitter and AgentToolFilter mixins."""

from __future__ import annotations

from typing import Any

from core.agent_mixins import AgentEventEmitter, AgentToolFilter
from core.events import RuntimeEvent, runtime_event_from_tuple


class _Emitter(AgentEventEmitter):
    pass


class _Filter(AgentToolFilter):
    pass


def test_default_callbacks_are_none() -> None:
    e = _Emitter()
    assert e._on_tuple_event is None
    assert e._on_runtime_event is None


def test_emit_tuple_forwards_to_listener() -> None:
    seen: list[tuple[str, dict[str, Any]]] = []
    e = _Emitter()
    e._on_tuple_event = lambda kind, data: seen.append((kind, data))
    e._emit_tuple("custom", {"x": 1})
    assert seen == [("custom", {"x": 1})]


def test_emit_tuple_swallows_listener_errors() -> None:
    e = _Emitter()

    def boom(kind: str, data: dict[str, Any]) -> None:
        raise RuntimeError("renderer broke")

    e._on_tuple_event = boom
    e._emit_tuple("custom", {})  # must not raise — rendering can't break the loop


def test_emit_tuple_is_noop_without_listener() -> None:
    _Emitter()._emit_tuple("custom", {})  # no listener, no error


def test_emit_runtime_forwards_to_listener() -> None:
    seen: list[RuntimeEvent] = []
    e = _Emitter()
    e._on_runtime_event = seen.append
    event = runtime_event_from_tuple("agent_start", {"tool_count": 0})
    assert event is not None
    e._emit_runtime(event)
    assert seen == [event]


def test_emit_runtime_swallows_listener_errors() -> None:
    e = _Emitter()

    def boom(event: RuntimeEvent) -> None:
        raise RuntimeError("renderer broke")

    e._on_runtime_event = boom
    event = runtime_event_from_tuple("agent_start", {"tool_count": 0})
    assert event is not None
    e._emit_runtime(event)  # must not raise


def test_emit_routes_unmapped_kind_to_tuple() -> None:
    tuple_seen: list[tuple[str, dict[str, Any]]] = []
    runtime_seen: list[RuntimeEvent] = []
    e = _Emitter()
    e._on_tuple_event = lambda kind, data: tuple_seen.append((kind, data))
    e._on_runtime_event = runtime_seen.append
    e._emit("zzz_unmapped_kind", {"k": "v"})
    assert tuple_seen == [("zzz_unmapped_kind", {"k": "v"})]
    assert runtime_seen == []


def test_emit_routes_mapped_kind_to_runtime() -> None:
    runtime_seen: list[RuntimeEvent] = []
    e = _Emitter()
    e._on_runtime_event = runtime_seen.append
    e._emit("agent_start", {"tool_count": 3})
    assert len(runtime_seen) == 1


def test_filter_tools_returns_the_same_list() -> None:
    tools: list[Any] = ["t1", "t2"]
    assert _Filter()._filter_tools(tools) is tools


def test_mixins_compose_in_one_class() -> None:
    # A single class can compose both mixins (as ConnectedInvestigationAgent does).
    class _Composed(AgentEventEmitter, AgentToolFilter):
        pass

    seen: list[tuple[str, dict[str, Any]]] = []
    c = _Composed()
    c._on_tuple_event = lambda kind, data: seen.append((kind, data))
    c._emit("zzz_unmapped_kind", {"a": 1})
    assert seen == [("zzz_unmapped_kind", {"a": 1})]

    tools: list[Any] = [1, 2, 3]
    assert c._filter_tools(tools) is tools
