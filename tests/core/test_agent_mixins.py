"""Unit tests for the AgentEventEmitter, AgentToolFilter, and AgentSteering mixins."""

from __future__ import annotations

from collections import deque
from typing import Any

from core.agent_mixins import AgentEventEmitter, AgentSteering, AgentToolFilter
from core.events import RuntimeEvent, runtime_event_from_tuple
from core.messages import UserRuntimeMessage


class _Emitter(AgentEventEmitter):
    pass


class _Filter(AgentToolFilter):
    pass


class _Steering(AgentSteering):
    def __init__(self) -> None:
        self._steering_messages: deque[str] = deque()
        self._follow_up_messages: deque[str] = deque()


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


def test_steer_queues_message_drained_into_transcript() -> None:
    s = _Steering()
    s.steer("look at the newest deploy first")
    messages: list[Any] = []
    s._drain_steering_messages(messages)
    assert len(messages) == 1
    assert isinstance(messages[0], UserRuntimeMessage)
    assert messages[0].content == "look at the newest deploy first"


def test_steer_ignores_blank_message() -> None:
    s = _Steering()
    s.steer("   ")
    messages: list[Any] = []
    s._drain_steering_messages(messages)
    assert messages == []


def test_drain_steering_messages_empties_the_queue() -> None:
    s = _Steering()
    s.steer("first")
    s.steer("second")
    messages: list[Any] = []
    s._drain_steering_messages(messages)
    assert [m.content for m in messages] == ["first", "second"]
    more: list[Any] = []
    s._drain_steering_messages(more)
    assert more == []


def test_follow_up_pop_returns_none_when_empty() -> None:
    s = _Steering()
    assert s._pop_follow_up_message() is None


def test_follow_up_pop_returns_queued_message_once() -> None:
    s = _Steering()
    s.follow_up("now summarize the remediation")
    assert s._pop_follow_up_message() == "now summarize the remediation"
    assert s._pop_follow_up_message() is None


def test_follow_up_ignores_blank_message() -> None:
    s = _Steering()
    s.follow_up("  ")
    assert s._pop_follow_up_message() is None
