from __future__ import annotations

import pytest

from core.agent.run_io import AgentRunResult
from platform.analytics import cli
from platform.analytics.events import Event
from platform.analytics.react_turn import emit_react_turn_completed, resolve_react_stop_reason


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
        ({"hit_iteration_cap": False, "tool_calls_executed": 2}, "completed"),
        ({"hit_iteration_cap": True, "tool_calls_executed": 2}, "iteration_cap"),
        ({"hit_iteration_cap": False, "tool_calls_executed": 0}, "no_tools_needed"),
        ({"hit_iteration_cap": False, "tool_calls_executed": 0, "error": RuntimeError()}, "error"),
        ({"hit_iteration_cap": False, "tool_calls_executed": 0, "cancelled": True}, "cancelled"),
    ],
)
def test_resolve_react_stop_reason(kwargs: dict[str, object], expected: str) -> None:
    assert resolve_react_stop_reason(**kwargs) == expected  # type: ignore[arg-type]


def test_capture_react_turn_completed_emits_required_properties(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    stub = _StubAnalytics()
    monkeypatch.setattr(cli, "get_analytics", lambda: stub)

    cli.capture_react_turn_completed(
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
        "platform.analytics.react_turn.capture_react_turn_completed",
        lambda **kwargs: captured.append(kwargs),
    )

    emit_react_turn_completed(
        phase="gather",
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
