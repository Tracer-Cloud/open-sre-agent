"""Core does not paint quiet shell stdout. That is a surface concern.

The action closer still drops a single-step self-recording closing. Quiet
stdout is buffered by the shell tool and shown by the REPL sink when the turn
would otherwise be blank.
"""

from __future__ import annotations

import json
from typing import Any

from core.agent_harness.turns.action_driver import _compose_response, _TurnCounts
from core.llm.types import ToolCall


class _ToolResult:
    def __init__(self, payload: dict[str, Any]) -> None:
        self.content = json.dumps(payload)
        self.is_error = False


class _Result:
    def __init__(
        self,
        *,
        tool_results: list[tuple[ToolCall, _ToolResult]],
        final_text: str = "",
    ) -> None:
        self.tool_results = tool_results
        self.executed = list(tool_results)
        self.final_text = final_text
        self.planned = [call for call, _ in tool_results]


class _Session:
    def __init__(self) -> None:
        self.history: list[dict[str, Any]] = []


def _shell_call(call_id: str, command: str, *, quiet: bool) -> ToolCall:
    return ToolCall(id=call_id, name="shell_run", input={"command": command, "quiet": quiet})


def _payload(response_text: str) -> dict[str, Any]:
    return {"ok": True, "response_text": response_text}


def _counts(steps: int) -> _TurnCounts:
    return _TurnCounts(
        executed_entries=[],
        executed_count=steps,
        executed_success_count=steps,
        generic_success_count=0,
        planned_count=steps,
        handled=True,
        investigation_dispatched=False,
        handoff_contents=(),
    )


def test_single_quiet_shell_run_does_not_put_stdout_in_display_chunks() -> None:
    # Arrange: one quiet step, whose closing the single-step rule drops.
    call = _shell_call("1", "echo hi", quiet=True)
    result = _Result(
        tool_results=[(call, _ToolResult(_payload("hi")))],
        final_text="Command completed successfully.",
    )

    # Act
    _response_text, display_chunks, _use_final_text = _compose_response(
        result, _Session(), _counts(1)
    )

    # Assert: core does not reprint stdout; the dropped closing stays dropped.
    assert "\n".join(display_chunks) == ""
    assert "Command completed successfully." not in _response_text


def test_quiet_probes_stay_hidden_when_a_composed_closing_is_shown() -> None:
    # Arrange: a multi-step chain keeps its closing, so the probes stay hidden.
    closing = "Amsterdam: sunny. Top story: markets open higher."
    result = _Result(
        tool_results=[
            (
                _shell_call("1", "curl wttr.in", quiet=True),
                _ToolResult(_payload("Amsterdam: +18C")),
            ),
            (
                _shell_call("2", "curl news", quiet=True),
                _ToolResult(_payload("Markets open higher")),
            ),
        ],
        final_text=closing,
    )

    # Act
    _response_text, display_chunks, _use_final_text = _compose_response(
        result, _Session(), _counts(2)
    )

    # Assert: the composed answer only — no raw curl output under it.
    shown = "\n".join(display_chunks)
    assert closing in shown
    assert "Amsterdam: +18C" not in shown
    assert "Markets open higher" not in shown


def test_loud_shell_run_does_not_reprint_stdout_in_display_chunks() -> None:
    # Arrange: a non-quiet step, whose stdout the runner already painted.
    call = _shell_call("1", "echo hi", quiet=False)
    result = _Result(
        tool_results=[(call, _ToolResult(_payload("hi")))],
        final_text="done",
    )

    # Act
    _response_text, display_chunks, _use_final_text = _compose_response(
        result, _Session(), _counts(1)
    )

    # Assert: nothing to show, so the turn cannot print stdout twice.
    assert "\n".join(display_chunks) == ""


def test_silent_tool_turn_prefers_paint_quiet_stdout_over_blank_print() -> None:
    """Surface-buffered quiet stdout is painted; empty print is only the fallback."""
    from core.agent_harness.turns.action_driver import _end_silent_tool_turn

    painted: list[str] = []
    printed: list[str] = []

    class _Sink:
        def paint_quiet_stdout(self) -> bool:
            painted.append("ok")
            return True

        def print(self, message: str = "") -> None:
            printed.append(message)

    _end_silent_tool_turn(_Sink())  # type: ignore[arg-type]

    assert painted == ["ok"]
    assert printed == []


def test_silent_tool_turn_falls_back_to_blank_print_without_paint_hook() -> None:
    from core.agent_harness.turns.action_driver import _end_silent_tool_turn

    printed: list[str] = []

    class _Sink:
        def print(self, message: str = "") -> None:
            printed.append(message)

    _end_silent_tool_turn(_Sink())  # type: ignore[arg-type]

    assert printed == [""]


def test_a_generic_tool_result_is_not_replaced_by_quiet_stdout() -> None:
    # Arrange: a registry tool answers the turn while a quiet shell step probes.
    github = ToolCall(id="1", name="github_cli", input={"command": "run list"})
    result = _Result(
        tool_results=[
            (github, _ToolResult(_payload("3 failed, 59 succeeded"))),
            (
                _shell_call("2", "gh api rate_limit", quiet=True),
                _ToolResult(_payload("rate limit 4998")),
            ),
        ],
        final_text="Here is the run list.",
    )

    # Act
    _response_text, display_chunks, _use_final_text = _compose_response(
        result, _Session(), _counts(2)
    )

    # Assert: the tool's own answer stands; the probe does not displace it.
    shown = "\n".join(display_chunks)
    assert "3 failed, 59 succeeded" in shown
    assert "rate limit 4998" not in shown
