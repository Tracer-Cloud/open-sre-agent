from __future__ import annotations

import io

from rich.console import Console

from interactive_shell.harness.orchestration.agent_actions import (
    TerminalActionExecutionResult,
)
from interactive_shell.runtime import controller as loop_controller
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


def test_execute_routed_turn_cli_agent_empty_response_is_recorded_empty(
    monkeypatch,
) -> None:
    recorder = _FakeRecorder()
    monkeypatch.setattr(loop_controller.PromptRecorder, "start", lambda **_kwargs: recorder)
    monkeypatch.setattr(
        loop_controller,
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
        loop_controller,
        "_answer_cli_agent_with_tools",
        lambda *_args, **_kwargs: LlmRunInfo(response_text=""),
    )

    session = ReplSession()
    output = io.StringIO()
    loop_controller.execute_routed_turn(
        "show datadog integration details",
        session,
        Console(file=output, force_terminal=False, highlight=False),
    )

    assert output.getvalue() == ""
    assert recorder.responses == [""]
    assert session.last_assistant_intent == "cli_agent_fallback"
