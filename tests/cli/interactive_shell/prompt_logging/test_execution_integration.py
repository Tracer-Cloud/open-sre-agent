from __future__ import annotations

import io

from rich.console import Console

from app.cli.interactive_shell.prompt_logging import LlmRunInfo
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
