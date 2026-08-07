"""Host cooperative cancel short-circuits gather/answer and labels the turn."""

from __future__ import annotations

import threading
from typing import Any

from core.agent_harness.session import SessionCore
from core.agent_harness.session.persistence.memory import InMemorySessionStorage
from core.agent_harness.turns.host_cancel import host_cancel_requested
from core.agent_harness.turns.orchestrator import run_turn
from core.agent_harness.turns.turn_results import (
    FINAL_INTENT_CANCELLED,
    ToolCallingTurnResult,
    TurnResult,
)


class _Accounting:
    def record_action_result(self, _result: ToolCallingTurnResult) -> None:
        return None

    def finalize(self, result: TurnResult) -> TurnResult:
        return result


class _CancelSink:
    def __init__(self) -> None:
        self.turn_cancel = threading.Event()
        self.streamed: list[str] = []

    def print(self, message: str = "") -> None:
        _ = message

    def render_response_header(self, label: str) -> None:
        _ = label

    def render_error(self, message: str) -> None:
        _ = message

    def stream(self, *, label: str, chunks: Any, **_kwargs: Any) -> str:
        _ = label
        parts = list(chunks)
        text = "".join(str(p) for p in parts)
        self.streamed.append(text)
        return text

    def finalize(self, text: str) -> None:
        _ = text


def test_host_cancel_requested_reads_sink_event() -> None:
    from core.agent_harness.turns.host_cancel import ensure_turn_cancel

    sink = _CancelSink()
    assert host_cancel_requested(sink) is False
    ensure_turn_cancel(sink).set()
    assert host_cancel_requested(sink) is True
    assert host_cancel_requested(None) is False


def test_run_turn_cancelled_action_skips_gather_and_answer() -> None:
    answer_calls: list[str] = []
    gather_calls: list[str] = []

    def execute_actions(_text: str, **_kwargs: object) -> ToolCallingTurnResult:
        return ToolCallingTurnResult(
            0,
            0,
            0,
            False,
            False,
            cancelled=True,
        )

    def answer(text: str, _request: object = None, **_kwargs: object) -> object:
        answer_calls.append(text)
        return type("Run", (), {"response_text": "should-not-run"})()

    def gather(text: str, **_kwargs: object) -> None:
        gather_calls.append(text)
        return None

    result = run_turn(
        "question?",
        SessionCore(storage=InMemorySessionStorage()),
        execute_actions=execute_actions,
        answer=answer,
        gather=gather,
        accounting=_Accounting(),
        output=_CancelSink(),
    )
    assert result.final_intent == FINAL_INTENT_CANCELLED
    assert result.cancelled is True
    assert result.answered is False
    assert answer_calls == []
    assert gather_calls == []


def test_run_turn_cancel_during_gather_skips_answer() -> None:
    sink = _CancelSink()
    answer_calls: list[str] = []

    def execute_actions(_text: str, **_kwargs: object) -> ToolCallingTurnResult:
        return ToolCallingTurnResult(0, 0, 0, False, False)

    def gather(_text: str, **_kwargs: object) -> str:
        sink.turn_cancel.set()
        return "evidence"

    def answer(text: str, _request: object = None, **_kwargs: object) -> object:
        answer_calls.append(text)
        return type("Run", (), {"response_text": "should-not-run"})()

    result = run_turn(
        "question?",
        SessionCore(storage=InMemorySessionStorage()),
        execute_actions=execute_actions,
        answer=answer,
        gather=gather,
        accounting=_Accounting(),
        output=sink,
    )
    assert result.final_intent == FINAL_INTENT_CANCELLED
    assert answer_calls == []


def test_live_sink_stream_stops_when_turn_cancel_set() -> None:
    from gateway.core.runtime.live_sink import LiveOutputSink

    class _Inner:
        def __init__(self) -> None:
            self.turn_cancel = threading.Event()
            self.seen: list[str] = []

        def stream(self, *, label: str, chunks: Any, **_kwargs: Any) -> str:
            _ = label
            parts: list[str] = []
            for chunk in chunks:
                self.seen.append(str(chunk))
                parts.append(str(chunk))
            return "".join(parts)

        def finalize(self, text: str) -> None:
            _ = text

        def render_error(self, message: str) -> None:
            _ = message

        def set_tool_status(self, text: str) -> None:
            _ = text

    inner = _Inner()
    live = LiveOutputSink()
    live.bind(inner)  # type: ignore[arg-type]

    def _chunks() -> Any:
        yield "a"
        inner.turn_cancel.set()
        yield "b"
        yield "c"

    text = live.stream(label="assistant", chunks=_chunks())
    assert text == "a"
    assert inner.seen == ["a"]
