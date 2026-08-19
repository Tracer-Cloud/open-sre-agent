"""Results that skipped live printing must still show stdout when the closing is dropped.

Tools that withhold live output set ``displayed`` to false on the payload. For a
single self-recording step the action closing is also dropped. ``display_chunks``
must then carry the tool's ``response_text``, or the REPL is a blank line.
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


def _payload(response_text: str, *, displayed: bool | None = None) -> dict[str, Any]:
    payload: dict[str, Any] = {"ok": True, "response_text": response_text}
    if displayed is not None:
        payload["displayed"] = displayed
    return payload


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


def test_single_quiet_shell_run_shows_response_text() -> None:
    # Arrange: one quiet step, whose closing the single-step rule drops.
    call = _shell_call("1", "echo hi", quiet=True)
    result = _Result(
        tool_results=[(call, _ToolResult(_payload("hi", displayed=False)))],
        final_text="Command completed successfully.",
    )

    # Act
    _response_text, display_chunks, _use_final_text = _compose_response(
        result, _Session(), _counts(1)
    )

    # Assert: the withheld stdout is shown, and the dropped closing is not.
    shown = "\n".join(display_chunks)
    assert "hi" in shown
    assert "Command completed successfully." not in shown


def test_quiet_probes_stay_hidden_when_a_composed_closing_is_shown() -> None:
    # Arrange: a multi-step chain keeps its closing, so the probes stay hidden.
    closing = "Amsterdam: sunny. Top story: markets open higher."
    result = _Result(
        tool_results=[
            (
                _shell_call("1", "curl wttr.in", quiet=True),
                _ToolResult(_payload("Amsterdam: +18C", displayed=False)),
            ),
            (
                _shell_call("2", "curl news", quiet=True),
                _ToolResult(_payload("Markets open higher", displayed=False)),
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
        tool_results=[(call, _ToolResult(_payload("hi", displayed=True)))],
        final_text="done",
    )

    # Act
    _response_text, display_chunks, _use_final_text = _compose_response(
        result, _Session(), _counts(1)
    )

    # Assert: nothing to show, so the turn cannot print stdout twice.
    assert "\n".join(display_chunks) == ""


def test_a_generic_tool_result_is_not_replaced_by_quiet_stdout() -> None:
    # Arrange: a registry tool answers the turn while a quiet shell step probes.
    github = ToolCall(id="1", name="github_cli", input={"command": "run list"})
    result = _Result(
        tool_results=[
            (github, _ToolResult(_payload("3 failed, 59 succeeded"))),
            (
                _shell_call("2", "gh api rate_limit", quiet=True),
                _ToolResult(_payload("rate limit 4998", displayed=False)),
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


def test_undisplayed_result_does_not_depend_on_the_tool_name() -> None:
    # Arrange: a self-recording tool other than shell_run withheld live output.
    call = ToolCall(id="1", name="slash_invoke", input={"command": "/health"})
    result = _Result(
        tool_results=[(call, _ToolResult(_payload("Oslo: snow", displayed=False)))],
        final_text="Command completed successfully.",
    )

    # Act
    _response_text, display_chunks, _use_final_text = _compose_response(
        result, _Session(), _counts(1)
    )

    # Assert
    shown = "\n".join(display_chunks)
    assert "Oslo: snow" in shown
    assert "Command completed successfully." not in shown


def test_quiet_on_the_call_without_displayed_false_does_not_reprint() -> None:
    # Arrange: core must not sniff shell_run/quiet; only the payload flag counts.
    call = _shell_call("1", "echo hi", quiet=True)
    result = _Result(
        tool_results=[(call, _ToolResult(_payload("hi")))],
        final_text="done",
    )

    # Act
    _response_text, display_chunks, _use_final_text = _compose_response(
        result, _Session(), _counts(1)
    )

    # Assert
    assert "\n".join(display_chunks) == ""
