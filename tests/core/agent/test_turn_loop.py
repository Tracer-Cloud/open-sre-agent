"""Shell-local turn loop bookkeeping tests."""

from __future__ import annotations

import io
from collections.abc import Callable
from typing import Any

from rich.console import Console

from core.agent_harness import OutputSink
from core.agent_harness.accounting.turn_accounting import DefaultTurnAccounting
from core.agent_harness.ports import ConfirmFn
from core.agent_harness.runtime import TurnPlan
from core.agent_harness.session.persistence.memory import InMemorySessionStore
from core.agent_harness.turns.orchestrator import run_turn
from core.tool import ToolExecutionHooks
from surfaces.interactive_shell.runtime.core.turn_accounting import (
    ToolCallingTurnResult,
)
from surfaces.interactive_shell.session import Session
from surfaces.interactive_shell.telemetry.recorder import LlmRunInfo
from tests.shared.harness_turn_driver import run_harness_turn


class _Recorder:
    def __init__(self) -> None:
        self.responses: list[tuple[str, LlmRunInfo | None]] = []
        self.flush_count = 0

    def set_response(self, response: str, run_info: LlmRunInfo | None = None) -> None:
        self.responses.append((response, run_info))

    def flush(self) -> None:
        self.flush_count += 1


def _console() -> Console:
    return Console(file=io.StringIO(), force_terminal=False, color_system=None, width=80)


def _unhandled_turn(
    message: str,
    session: Session,
    console: Console,
    *,
    confirm_fn: ConfirmFn | None = None,
    is_tty: bool | None = None,
    request_exit: Callable[[], None] | None = None,
    turn_plan: TurnPlan | None = None,
    output: OutputSink | None = None,
    tool_hooks: ToolExecutionHooks | None = None,
) -> ToolCallingTurnResult:
    """A RunActionToolTurn seam whose action turn handles nothing."""
    return ToolCallingTurnResult(
        planned_count=0,
        executed_count=0,
        executed_success_count=0,
        has_unhandled_clause=False,
        handled=False,
        response_text="answered",
    )


def test_recorder_flushes_once_for_agent_answer() -> None:
    recorder = _Recorder()

    result = run_harness_turn(
        "question",
        Session(),
        _console(),
        recorder=recorder,  # type: ignore[arg-type]
        execute_actions=_unhandled_turn,
    )

    assert result.answered is True
    assert result.assistant_response_text == "answered"
    assert recorder.responses == [("answered", None)]
    assert recorder.flush_count == 1


def test_recorder_flushes_once_for_silent_handled_turn() -> None:
    recorder = _Recorder()
    session = Session()

    def _handled(
        message: str,
        session: Session,
        console: Console,
        *,
        confirm_fn: ConfirmFn | None = None,
        is_tty: bool | None = None,
        request_exit: Callable[[], None] | None = None,
        turn_plan: TurnPlan | None = None,
        output: OutputSink | None = None,
        tool_hooks: ToolExecutionHooks | None = None,
    ) -> ToolCallingTurnResult:
        """A RunActionToolTurn seam whose action turn handles the request."""
        return ToolCallingTurnResult(
            planned_count=1,
            executed_count=1,
            executed_success_count=1,
            has_unhandled_clause=False,
            handled=True,
            response_text="command output",
        )

    result = run_harness_turn(
        "run something",
        session,
        _console(),
        recorder=recorder,  # type: ignore[arg-type]
        execute_actions=_handled,
    )

    assert result.answered is True
    assert result.final_intent == "agent_completed"
    assert recorder.responses == [("command output", None)]
    assert recorder.flush_count == 1
    assert session.cli_agent_messages[-2:] == [
        ("user", "run something"),
        ("assistant", "command output"),
    ]


def test_default_turn_accounting_persists_action_only_context() -> None:
    storage = InMemorySessionStore()
    session = Session(store=storage)
    storage.open_session(session)

    def _handled(
        text: str,
        *,
        confirm_fn: ConfirmFn | None = None,
        is_tty: bool | None = None,
        turn_plan: Any = None,
    ) -> ToolCallingTurnResult:
        """An ExecuteActions seam whose action turn handles the request."""
        return ToolCallingTurnResult(
            planned_count=1,
            executed_count=1,
            executed_success_count=1,
            has_unhandled_clause=False,
            handled=True,
            response_text="Hawaii: +28C",
        )

    result = run_turn(
        "weather in Hawaii",
        session,
        execute_actions=_handled,
        accounting=DefaultTurnAccounting(session, "weather in Hawaii"),
    )

    records = storage.read(session.session_id)
    messages = [record for record in records if record.get("type") == "message"]

    assert result.final_intent == "agent_completed"
    assert session.cli_agent_messages[-2:] == [
        ("user", "weather in Hawaii"),
        ("assistant", "Hawaii: +28C"),
    ]
    assert [
        (message.get("role"), message.get("content"), message.get("metadata"))
        for message in messages[-2:]
    ] == [
        ("user", "weather in Hawaii", {"kind": "chat"}),
        ("assistant", "Hawaii: +28C", {"kind": "chat"}),
    ]
