"""Characterization: AgentSession is the public host API (chat + investigate)."""

from __future__ import annotations

from typing import Any

import pytest

from core.agent_harness.harness import (
    AgentHarness,
    AgentSession,
    HarnessConfig,
    SessionConfig,
)
from core.agent_harness.investigation_api import (
    InvestigationResult,
    install_investigation_payload_runner,
    reset_investigation_payload_runner_for_tests,
)
from core.agent_harness.turns.turn_results import ToolCallingTurnResult, TurnResult


def test_session_config_aliases_harness_config() -> None:
    assert SessionConfig is HarnessConfig
    assert AgentSession is AgentHarness


def _stub_turn_result() -> TurnResult:
    return TurnResult(
        final_intent="answered",
        action_result=ToolCallingTurnResult(
            planned_count=0,
            executed_count=0,
            executed_success_count=0,
            has_unhandled_clause=False,
            handled=True,
        ),
        assistant_response_text="ok",
        llm_run=object(),
    )


def test_chat_is_the_public_verb(monkeypatch: Any) -> None:
    session = AgentSession.start()
    captured: list[str] = []

    def _fake_dispatch(message: str) -> TurnResult:
        captured.append(message)
        return _stub_turn_result()

    assert session.agent is not None
    monkeypatch.setattr(session.agent, "dispatch", _fake_dispatch)

    result = session.chat("why is checkout-api slow?")
    assert captured == ["why is checkout-api slow?"]
    assert result.answered is True
    # Compatibility alias
    session.dispatch_message("follow-up")
    assert captured == ["why is checkout-api slow?", "follow-up"]


def test_investigate_uses_installed_runner() -> None:
    reset_investigation_payload_runner_for_tests()

    def _fake_runner(
        *,
        raw_alert: Any,
        opensre_evaluate: bool = False,
        investigation_metadata: tuple[str, str] | None = None,
    ) -> dict[str, Any]:
        assert raw_alert == {"alert_name": "HighLatency"}
        assert opensre_evaluate is False
        assert investigation_metadata == ("HighLatency", "warning")
        return {
            "report": "done",
            "problem_md": "p",
            "root_cause": "r",
            "is_noise": False,
            "validity_score": 0.9,
        }

    install_investigation_payload_runner(_fake_runner)
    try:
        result = AgentSession().investigate(
            {"alert_name": "HighLatency"},
            investigation_metadata=("HighLatency", "warning"),
        )
        assert isinstance(result, InvestigationResult)
        assert result.report == "done"
        assert result.root_cause == "r"
        assert result.as_dict()["report"] == "done"
    finally:
        reset_investigation_payload_runner_for_tests()


def test_investigate_fails_closed_without_runner() -> None:
    reset_investigation_payload_runner_for_tests()
    with pytest.raises(RuntimeError, match="Investigation payload runner is not installed"):
        AgentSession().investigate("spike")


def test_investigation_result_round_trips_optional_fields() -> None:
    payload = {
        "report": "r",
        "problem_md": "p",
        "root_cause": "c",
        "is_noise": True,
        "validity_score": 0.1,
        "tool_calls": [{"name": "x"}],
        "opensre_llm_eval": {"score": 1},
        "custom": 42,
    }
    result = InvestigationResult.from_payload(payload)
    assert result.as_dict() == payload
