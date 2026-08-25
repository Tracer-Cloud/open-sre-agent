"""Regression: the answer route must run even before stream-rewrite bookkeeping."""

from __future__ import annotations

from typing import Any

from core.agent_harness.accounting.turn_accounting import DefaultTurnAccounting
from core.agent_harness.ports import AnswerRequest
from core.agent_harness.session.session_core import SessionCore
from core.agent_harness.turns.orchestrator import run_turn
from core.agent_harness.turns.turn_results import ToolCallingTurnResult


def test_answer_route_does_not_depend_on_stream_rewrite_flag() -> None:
    """``text_changed_after_streaming`` is post-answer bookkeeping only.

    Gating the answer route on that flag raised UnboundLocalError before
    answer ran (live oracle 346). The route must answer unconditionally.
    """
    session = SessionCore()
    answer_calls: list[str] = []

    def _execute(*_a: object, **_k: object) -> ToolCallingTurnResult:
        return ToolCallingTurnResult(
            planned_count=0,
            executed_count=0,
            executed_success_count=0,
            has_unhandled_clause=True,
            handled=False,
            handoff_contents=("chat:greeting", "Say hello."),
        )

    def _answer(text: str, request: AnswerRequest, **_k: Any) -> Any:
        answer_calls.append(text)
        return type("Run", (), {"response_text": "Hello from the answer route."})()

    result = run_turn(
        "hi",
        session,
        execute_actions=_execute,
        answer=_answer,
        accounting=DefaultTurnAccounting(session, "hi"),
    )

    assert answer_calls == ["hi"]
    assert result.final_intent == "cli_agent_fallback"
    assert "Hello from the answer route" in (result.assistant_response_text or "")
    assert result.gather_success_count == 0
