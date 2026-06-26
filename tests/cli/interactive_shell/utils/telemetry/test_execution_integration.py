from __future__ import annotations

import io

from rich.console import Console

from cli.interactive_shell.harness.orchestration.agent_actions import (
    TerminalActionExecutionResult,
)
from cli.interactive_shell.harness.types import RouteDecision, RouteKind
from cli.interactive_shell.runtime import execution
from cli.interactive_shell.runtime.session import ReplSession
from cli.interactive_shell.utils.telemetry import LlmRunInfo


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


def test_execute_routed_turn_cli_agent_empty_response_prints_deterministic_fallback(
    monkeypatch,
) -> None:
    recorder = _FakeRecorder()
    monkeypatch.setattr(execution.PromptRecorder, "start", lambda **_kwargs: recorder)
    monkeypatch.setattr(
        execution,
        "execute_cli_actions",
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
    decision = RouteDecision(RouteKind.HANDLE_MESSAGE_WITH_AGENT, 0.9, ())
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
