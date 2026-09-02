from __future__ import annotations

import pytest

from core.agent.run_io import AgentRunResult
from infrastructure.analytics import capture
from infrastructure.analytics.events import Event
from infrastructure.analytics.react_turn import (
    ReactPhase,
    ReactStopReason,
    emit_react_turn_completed,
    resolve_react_stop_reason,
)


class _StubLLM:
    _model = "claude-sonnet-4-6"
    _provider_label = "Anthropic"


class _StubAnalytics:
    def __init__(self) -> None:
        self.events: list[tuple[Event, dict[str, object] | None]] = []

    def capture(self, event: Event, properties: dict[str, object] | None = None) -> None:
        self.events.append((event, properties))


@pytest.mark.parametrize(
    ("kwargs", "expected"),
    [
        ({"hit_iteration_cap": False, "tool_calls_executed": 2}, ReactStopReason.COMPLETED),
        ({"hit_iteration_cap": True, "tool_calls_executed": 2}, ReactStopReason.ITERATION_CAP),
        ({"hit_iteration_cap": False, "tool_calls_executed": 0}, ReactStopReason.NO_TOOLS_NEEDED),
        (
            {"hit_iteration_cap": False, "tool_calls_executed": 0, "error": RuntimeError()},
            ReactStopReason.ERROR,
        ),
        (
            {"hit_iteration_cap": False, "tool_calls_executed": 0, "cancelled": True},
            ReactStopReason.CANCELLED,
        ),
    ],
)
def test_resolve_react_stop_reason(kwargs: dict[str, object], expected: ReactStopReason) -> None:
    result = resolve_react_stop_reason(**kwargs)  # type: ignore[arg-type]
    assert result is expected


def test_react_stop_reason_round_trips_from_analytics_string() -> None:
    for member in ReactStopReason:
        assert ReactStopReason(str(member)) is member
        assert member == member.value

    assert ReactPhase("action") is ReactPhase.ACTION


def test_capture_react_turn_completed_emits_required_properties(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    stub = _StubAnalytics()
    monkeypatch.setattr(capture, "get_analytics", lambda: stub)

    capture.capture_react_turn_completed(
        phase="action",
        llm_iterations_used=3,
        llm_iteration_cap=6,
        hit_iteration_cap=False,
        stop_reason="completed",
        tool_calls_executed=2,
        duration_ms=1200,
        cli_session_id="sess-1",
        cli_turn_kind="agent",
        llm_provider="anthropic",
        llm_model="claude-sonnet-4-6",
        investigation_id="inv-1",
        investigation_loop_count=2,
        prompt_turn_id="turn-1",
    )

    assert stub.events == [
        (
            Event.REACT_TURN_COMPLETED,
            {
                "phase": "action",
                "llm_iterations_used": 3,
                "llm_iteration_cap": 6,
                "hit_iteration_cap": False,
                "stop_reason": "completed",
                "tool_calls_executed": 2,
                "duration_ms": 1200,
                "cli_session_id": "sess-1",
                "cli_turn_kind": "agent",
                "llm_provider": "anthropic",
                "llm_model": "claude-sonnet-4-6",
                "investigation_id": "inv-1",
                "investigation_loop_count": 2,
                "prompt_turn_id": "turn-1",
            },
        )
    ]


def test_emit_react_turn_completed_sets_hit_iteration_cap_from_stop_reason(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: list[dict[str, object]] = []
    monkeypatch.setattr(
        "infrastructure.analytics.react_turn.capture_react_turn_completed",
        lambda **kwargs: captured.append(kwargs),
    )

    emit_react_turn_completed(
        phase=ReactPhase.GATHER,
        result=AgentRunResult(
            messages=[],
            final_text="",
            hit_iteration_cap=True,
            llm_iterations_used=4,
        ),
        iteration_cap=4,
        duration_ms=900,
        llm=_StubLLM(),
        session=None,
    )

    assert captured == [
        {
            "phase": "gather",
            "llm_iterations_used": 4,
            "llm_iteration_cap": 4,
            "hit_iteration_cap": True,
            "stop_reason": "iteration_cap",
            "tool_calls_executed": 0,
            "duration_ms": 900,
            "cli_session_id": "",
            "cli_turn_kind": "agent",
            "llm_provider": "anthropic",
            "llm_model": "claude-sonnet-4-6",
            "investigation_id": None,
            "investigation_loop_count": None,
            "prompt_turn_id": None,
        }
    ]
