"""Characterization: AgentSession is the public host API (chat + investigate)."""

from __future__ import annotations

from typing import Any
from unittest.mock import patch

import pytest

from core.agent_harness.harness import AgentSession
from core.agent_harness.investigation_api import (
    InvestigationResult,
    install_investigation_payload_runner,
    reset_investigation_payload_runner_for_tests,
)
from core.agent_harness.turns.turn_results import ToolCallingTurnResult, TurnResult


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
    session.chat("follow-up")
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


def test_one_shot_turn_reuses_the_start_bootstrap_instead_of_repeating_it() -> None:
    """``start`` is the single headless bootstrap; the one-shot delegates to it.

    ``start`` and ``run_headless_turn`` each used to run the same sequence —
    create session, resolve sink, resolve prompts, build the default agent,
    attach it — so a change to headless startup had to be made twice. Pinning
    the delegation keeps one bootstrap and makes the multi-turn shape
    (``start()`` once, ``chat()`` per turn) the same code path as the one-shot.
    """
    # Arrange: record every start() call and hand back a stub session.
    calls: list[dict[str, Any]] = []
    turns: list[str] = []

    class _StubSession:
        def chat(self, message: str, **_kwargs: Any) -> str:
            turns.append(message)
            return "answered"

    def _start(config=None, **kwargs: Any) -> Any:
        calls.append({"config": config, **kwargs})
        return _StubSession()

    bootstraps: list[str] = []

    def _startup(_self: Any) -> Any:
        bootstraps.append("startup")
        raise AssertionError("bootstrap ran outside start()")

    def _start_classmethod(_cls: Any, *args: Any, **kwargs: Any) -> Any:
        return _start(*args, **kwargs)

    with (
        patch.object(AgentSession, "start", classmethod(_start_classmethod)),
        patch.object(AgentSession, "startup", _startup),
    ):
        # Act.
        result = AgentSession.run_headless_turn("summarize open incidents")

    # Assert: exactly one bootstrap, and it happened inside start().
    assert len(calls) == 1, "run_headless_turn re-implemented the bootstrap"
    assert bootstraps == [], "run_headless_turn ran its own startup alongside start()"
    assert turns == ["summarize open incidents"]
    assert result == "answered"


def test_the_harness_exposes_one_name_per_concept() -> None:
    """No compatibility aliases: a second name for a live concept is drift waiting to happen.

    ``AgentHarness``/``HarnessConfig``/``HarnessStartupResult``/``dispatch_message``
    were removed once every caller moved. AGENTS.md forbids keeping
    compatibility-only paths after a refactor, so this fails if one returns.
    """
    # Arrange / Act.
    from core import agent_harness as package
    from core.agent_harness import harness as harness_module

    retired = ("AgentHarness", "HarnessConfig", "HarnessStartupResult")

    # Assert.
    for name in retired:
        assert not hasattr(harness_module, name), f"{name} alias is back in harness.py"
        assert not hasattr(package, name), f"{name} alias is back in the package API"
    assert not hasattr(AgentSession, "dispatch_message"), "dispatch_message alias is back"
