"""Regression: gather_and_answer must run even before stream-rewrite bookkeeping."""

from __future__ import annotations

from typing import Any

from core.agent_harness.accounting.turn_accounting import DefaultTurnAccounting
from core.agent_harness.ports import AnswerRequest
from core.agent_harness.session.session_core import SessionCore
from core.agent_harness.turns.gather_observation import GatheredEvidence
from core.agent_harness.turns.orchestrator import run_turn
from core.agent_harness.turns.turn_results import ToolCallingTurnResult


def test_gather_and_answer_route_does_not_depend_on_stream_rewrite_flag() -> None:
    """``text_changed_after_streaming`` is post-answer bookkeeping only.

    Gating ``gather_and_answer`` on that flag raised UnboundLocalError before
    answer ran (live oracle 346). The route must gather+answer unconditionally.
    """
    session = SessionCore()
    gather_calls: list[str] = []
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

    def _gather(text: str, *_a: object, **_k: object) -> str:
        gather_calls.append(text)
        return "no live evidence"

    def _answer(text: str, request: AnswerRequest, **_k: Any) -> Any:
        answer_calls.append(text)
        return type("Run", (), {"response_text": "Hello from gather_and_answer."})()

    result = run_turn(
        "hi",
        session,
        execute_actions=_execute,
        gather=_gather,
        answer=_answer,
        accounting=DefaultTurnAccounting(session, "hi"),
    )

    assert gather_calls == ["hi"]
    assert answer_calls == ["hi"]
    assert result.final_intent == "cli_agent_fallback"
    assert "Hello from gather_and_answer" in (result.assistant_response_text or "")
    assert result.gather_success_count == 0


def test_gather_and_answer_records_gather_success_count_on_turn_result() -> None:
    """Gather successes must land on ``TurnResult`` for SessionGoal evidence."""
    session = SessionCore()
    gather_calls: list[str] = []

    def _execute(*_a: object, **_k: object) -> ToolCallingTurnResult:
        return ToolCallingTurnResult(
            planned_count=0,
            executed_count=0,
            executed_success_count=0,
            has_unhandled_clause=True,
            handled=False,
            # Non-metric handoff so L0 missing-source does not skip gather.
            handoff_contents=("chat:greeting", "Say hello."),
        )

    def _gather(text: str, *_a: object, **_k: object) -> GatheredEvidence:
        gather_calls.append(text)
        return GatheredEvidence(
            observation="Tool: call_posthog_tool\nResult: windows|272",
            tool_results=(
                ("list_posthog_tools", {"ok": True}),
                ("call_posthog_tool", "windows|272"),
            ),
        )

    def _answer(*_a: object, **_k: Any) -> Any:
        return type("Run", (), {"response_text": "I found: 272 Windows users."})()

    result = run_turn(
        "How many Windows users?",
        session,
        execute_actions=_execute,
        gather=_gather,
        answer=_answer,
        accounting=DefaultTurnAccounting(session, "How many Windows users?"),
    )

    assert gather_calls == ["How many Windows users?"]
    # list_* does not count toward SessionGoal gather evidence — only the query.
    assert result.gather_success_count == 1
    assert result.action_result.executed_success_count == 0
