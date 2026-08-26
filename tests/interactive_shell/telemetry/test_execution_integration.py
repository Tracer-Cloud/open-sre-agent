from __future__ import annotations

import io

from rich.console import Console

from surfaces.interactive_shell.runtime.core.turn_accounting import (
    ToolCallingTurnResult,
)
from surfaces.interactive_shell.session import Session
from surfaces.interactive_shell.telemetry import LlmRunInfo
from tests.shared.harness_turn_driver import run_harness_turn


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


def test_run_harness_turn_cli_agent_empty_response_is_recorded_empty() -> None:
    recorder = _FakeRecorder()

    def fake_execute(*_args: object, **_kwargs: object) -> ToolCallingTurnResult:
        return ToolCallingTurnResult(
            planned_count=0,
            executed_count=0,
            executed_success_count=0,
            has_unhandled_clause=False,
            handled=False,
        )

    session = Session()
    output = io.StringIO()
    run_harness_turn(
        "show datadog integration details",
        session,
        Console(file=output, force_terminal=False, highlight=False),
        recorder=recorder,
        confirm_fn=None,
        is_tty=None,
        execute_actions=fake_execute,
    )

    assert output.getvalue() == ""
    assert recorder.responses == [""]
    assert session.last_assistant_intent == "agent_completed"
