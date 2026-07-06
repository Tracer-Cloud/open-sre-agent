"""Tests for ShellTurnAccounting's pending turn LLM/error consumption."""

from __future__ import annotations

from typing import Any

from core.agent_harness.accounting.token_accounting import LlmRunInfo
from core.agent_harness.models.turn_results import ShellTurnResult, ToolCallingTurnResult
from core.agent_harness.session import Session
from surfaces.interactive_shell.runtime.core.turn_accounting import ShellTurnAccounting


class _FakeRecorder:
    def __init__(self) -> None:
        self.errors: list[tuple[str, str]] = []
        self.responses: list[tuple[str, Any]] = []
        self.flushed = 0

    def set_error(self, kind: str, message: str) -> None:
        self.errors.append((kind, message))

    def set_response(self, text: str, run: Any | None = None) -> None:
        self.responses.append((text, run))

    def flush(self) -> None:
        self.flushed += 1


def _result(*, llm_run: Any | None = None, text: str = "done") -> ShellTurnResult:
    return ShellTurnResult(
        final_intent="slash",
        action_result=ToolCallingTurnResult(
            planned_count=0,
            executed_count=0,
            executed_success_count=0,
            has_unhandled_clause=False,
            handled=True,
        ),
        assistant_response_text=text,
        llm_run=llm_run,
    )


def test_finalize_applies_pending_turn_llm_when_no_conversational_run() -> None:
    session = Session()
    pending = LlmRunInfo(model="claude-sonnet-4-5", provider="anthropic", input_tokens=100)
    session.set_pending_turn_llm(pending)
    recorder = _FakeRecorder()
    accounting = ShellTurnAccounting(session=session, text="/investigate", recorder=recorder)  # type: ignore[arg-type]

    accounting.finalize(_result())

    assert recorder.responses == [("done", pending)]
    assert recorder.flushed == 1
    assert session.pop_pending_turn_llm() is None


def test_finalize_prefers_conversational_run_over_pending() -> None:
    session = Session()
    session.set_pending_turn_llm(LlmRunInfo(model="stale"))
    conversational = LlmRunInfo(model="fresh")
    recorder = _FakeRecorder()
    accounting = ShellTurnAccounting(session=session, text="hi", recorder=recorder)  # type: ignore[arg-type]

    accounting.finalize(_result(llm_run=conversational))

    assert recorder.responses[0][1] is conversational
    # The stale pending run was still consumed so it cannot leak later.
    assert session.pop_pending_turn_llm() is None


def test_finalize_sets_structured_error_from_pending_turn_error() -> None:
    session = Session()
    session.set_pending_turn_error("config", "ANTHROPIC_API_KEY not set")
    recorder = _FakeRecorder()
    accounting = ShellTurnAccounting(session=session, text="/investigate", recorder=recorder)  # type: ignore[arg-type]

    accounting.finalize(_result(text="investigation_failed"))

    assert recorder.errors == [("config", "ANTHROPIC_API_KEY not set")]
    assert session.pop_pending_turn_error() is None


def test_finalize_consumes_pending_state_even_without_recorder() -> None:
    session = Session()
    session.set_pending_turn_llm(LlmRunInfo(model="m"))
    session.set_pending_turn_error("llm", "boom")
    accounting = ShellTurnAccounting(session=session, text="hi", recorder=None)

    accounting.finalize(_result())

    assert session.pop_pending_turn_llm() is None
    assert session.pop_pending_turn_error() is None


def test_stage_investigation_turn_telemetry_populates_pending_state() -> None:
    from surfaces.interactive_shell.command_registry.investigation import (
        _stage_investigation_turn_telemetry,
    )
    from surfaces.interactive_shell.ui.investigation_outcome import InvestigationOutcome

    session = Session()
    outcome = InvestigationOutcome(
        status="failed",
        target="generic",
        investigation_id="inv-1",
        error_message="Anthropic authentication failed. Check ANTHROPIC_API_KEY.",
        failure_category="llm",
        llm_model="claude-sonnet-4-5",
        llm_provider="anthropic",
        llm_input_tokens=500,
        llm_output_tokens=120,
        duration_ms=4200,
    )

    _stage_investigation_turn_telemetry(session, outcome)

    run = session.pop_pending_turn_llm()
    assert run is not None
    assert run.model == "claude-sonnet-4-5"
    assert run.provider == "anthropic"
    assert run.input_tokens == 500
    assert run.output_tokens == 120
    assert run.latency_ms == 4200
    assert session.pop_pending_turn_error() == (
        "llm",
        "Anthropic authentication failed. Check ANTHROPIC_API_KEY.",
    )
    # Provider-measured tokens also count toward the session totals.
    assert session.tokens.totals["input"] == 500
    assert session.tokens.totals["output"] == 120


def test_stage_investigation_turn_telemetry_completed_run_has_no_error() -> None:
    from surfaces.interactive_shell.command_registry.investigation import (
        _stage_investigation_turn_telemetry,
    )
    from surfaces.interactive_shell.ui.investigation_outcome import InvestigationOutcome

    session = Session()
    _stage_investigation_turn_telemetry(
        session,
        InvestigationOutcome(
            status="completed",
            target="generic",
            investigation_id="inv-2",
            llm_model="claude-sonnet-4-5",
        ),
    )

    assert session.pop_pending_turn_llm() is not None
    assert session.pop_pending_turn_error() is None
