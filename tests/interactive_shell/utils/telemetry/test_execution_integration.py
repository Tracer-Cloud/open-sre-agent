from __future__ import annotations

import io

from rich.console import Console

from interactive_shell.harness.orchestration.agent_actions import (
    TerminalActionExecutionResult,
)
from interactive_shell.harness.pipeline import handle_message_with_agent
from interactive_shell.runtime.core.session import ReplSession
from interactive_shell.utils.telemetry import LlmRunInfo


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


def test_handle_message_with_agent_cli_agent_empty_response_is_recorded_empty() -> None:
    recorder = _FakeRecorder()

    def fake_execute(*_args: object, **_kwargs: object) -> TerminalActionExecutionResult:
        return TerminalActionExecutionResult(
            planned_count=0,
            executed_count=0,
            executed_success_count=0,
            has_unhandled_clause=False,
            handled=False,
        )

    def fake_answer(*_args: object, **_kwargs: object) -> LlmRunInfo:
        return LlmRunInfo(response_text="")

    session = ReplSession()
    output = io.StringIO()
    handle_message_with_agent(
        "show datadog integration details",
        session,
        Console(file=output, force_terminal=False, highlight=False),
        recorder=recorder,
        confirm_fn=None,
        is_tty=None,
        execute_actions=fake_execute,
        answer_agent=fake_answer,
    )

    assert output.getvalue() == ""
    assert recorder.responses == [""]
    assert session.last_assistant_intent == "cli_agent_fallback"
