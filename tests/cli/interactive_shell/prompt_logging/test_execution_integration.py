from __future__ import annotations

import io

from rich.console import Console

from app.cli.interactive_shell.prompt_logging import LlmRunInfo
from app.cli.interactive_shell.routing.handle_message_with_agent.orchestration.agent_actions import (
    TerminalActionExecutionResult,
)
from app.cli.interactive_shell.routing.types import RouteDecision, RouteKind
from app.cli.interactive_shell.runtime import execution
from app.cli.interactive_shell.runtime.session import ReplSession


class _FakeRecorder:
    def __init__(self) -> None:
        self.responses: list[str] = []
        self.flushed = False

    def set_response(self, text: str, _run: LlmRunInfo | None = None) -> None:
        self.responses.append(text)

    def flush(self) -> None:
        self.flushed = True


def _console() -> Console:
    return Console(file=io.StringIO(), force_terminal=False, highlight=False)


def test_execute_routed_turn_cli_help_records_prompt_response(monkeypatch) -> None:
    recorder = _FakeRecorder()
    monkeypatch.setattr(execution.PromptRecorder, "start", lambda **_kwargs: recorder)
    monkeypatch.setattr(
        execution,
        "answer_cli_help",
        lambda *_args, **_kwargs: LlmRunInfo(response_text="help response"),
    )

    session = ReplSession()
    decision = RouteDecision(RouteKind.CLI_HELP, 0.9, ())
    execution.execute_routed_turn(
        "how to deploy",
        session,
        _console(),
        on_exit=lambda: None,
        decision=decision,
    )
    assert recorder.responses == ["help response"]
    assert recorder.flushed is True


def test_execute_routed_turn_follow_up_records_prompt_response(monkeypatch) -> None:
    recorder = _FakeRecorder()
    monkeypatch.setattr(execution.PromptRecorder, "start", lambda **_kwargs: recorder)
    monkeypatch.setattr(
        execution,
        "answer_follow_up",
        lambda *_args, **_kwargs: LlmRunInfo(response_text="follow up response"),
    )
    session = ReplSession()
    session.last_state = {"root_cause": "x"}
    decision = RouteDecision(RouteKind.FOLLOW_UP, 0.9, ())
    execution.execute_routed_turn(
        "why?",
        session,
        _console(),
        on_exit=lambda: None,
        decision=decision,
    )
    assert recorder.responses == ["follow up response"]
    assert recorder.flushed is True


def test_execute_routed_turn_cli_agent_empty_response_prints_deterministic_fallback(
    monkeypatch,
) -> None:
    recorder = _FakeRecorder()
    monkeypatch.setattr(execution.PromptRecorder, "start", lambda **_kwargs: recorder)
    monkeypatch.setattr(
        execution,
        "execute_cli_actions_with_metrics",
        lambda *_args, **_kwargs: TerminalActionExecutionResult(
            planned_count=0,
            executed_count=0,
            executed_success_count=0,
            has_unhandled_clause=False,
            handled=False,
        ),
    )
    monkeypatch.setattr(
        execution,
        "answer_cli_agent",
        lambda *_args, **_kwargs: LlmRunInfo(response_text=""),
    )

    session = ReplSession()
    session.configured_integrations_known = True
    session.configured_integrations = ()
    decision = RouteDecision(RouteKind.CLI_AGENT, 0.9, ())
    output = io.StringIO()
    execution.execute_routed_turn(
        "show datadog integration details",
        session,
        Console(file=output, force_terminal=False, highlight=False),
        on_exit=lambda: None,
        decision=decision,
    )

    rendered = output.getvalue().lower()
    assert "datadog integration details" in rendered
    assert "integrations are configured" in rendered
    assert "investigate" in rendered
    assert recorder.responses
    assert "datadog integration details" in recorder.responses[0].lower()
    assert session.last_assistant_intent == "cli_agent_fallback"
