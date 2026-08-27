"""Quiet ``shell_run`` keeps the model closing — it withheld live stdout.

Loud single ``shell_run`` still suppresses closings (output is already on
screen). Quiet probes never enter display_chunks; the composed closing does.
"""

from __future__ import annotations

import json
from typing import Any

from core.agent_harness.turns.action_driver import _compose_response, _TurnCounts
from core.llm.types import ToolCall


class _ToolResult:
    def __init__(self, payload: dict[str, Any], *, is_error: bool = False) -> None:
        self.content = json.dumps(payload)
        self.details = payload
        self.is_error = is_error


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
        self.pending_user_choice: object | None = None

        class _Terminal:
            pending_choice_response: str | None = None

        self.terminal = _Terminal()


def _shell_call(call_id: str, command: str, *, quiet: bool) -> ToolCall:
    return ToolCall(id=call_id, name="shell_run", input={"command": command, "quiet": quiet})


def _payload(response_text: str) -> dict[str, Any]:
    return {"ok": True, "response_text": response_text}


def _counts(
    steps: int,
    *,
    executed_entries: list[dict[str, Any]] | None = None,
) -> _TurnCounts:
    return _TurnCounts(
        executed_entries=executed_entries or [],
        executed_count=steps,
        executed_success_count=steps,
        generic_success_count=0,
        planned_count=steps,
        handled=True,
        investigation_dispatched=False,
    )


def test_single_quiet_shell_run_keeps_the_model_closing() -> None:
    # Arrange: quiet withheld live stdout; the closing is the turn display.
    closing = "Amsterdam is +18C and clear."
    call = _shell_call("1", "curl wttr.in", quiet=True)
    result = _Result(
        tool_results=[(call, _ToolResult(_payload("Amsterdam: +18C")))],
        final_text=closing,
    )

    # Act
    _response_text, display_chunks, _use_final_text = _compose_response(
        result, _Session(), _counts(1)
    )

    # Assert: closing shown; raw probe stdout is not reprinted by core.
    shown = "\n".join(display_chunks)
    assert closing in shown
    assert "Amsterdam: +18C" not in shown


def test_quiet_string_false_still_suppresses_loud_closing() -> None:
    # Arrange: models sometimes emit quiet as a string; "false" must not keep closings.
    call = ToolCall(
        id="1",
        name="shell_run",
        input={"command": "echo hi", "quiet": "false"},
    )
    result = _Result(
        tool_results=[(call, _ToolResult(_payload("hi")))],
        final_text="done",
    )

    # Act
    _response_text, display_chunks, _use_final_text = _compose_response(
        result, _Session(), _counts(1)
    )

    # Assert: treated as loud — closing suppressed, no stdout reprint.
    assert "\n".join(display_chunks) == ""


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


def test_silent_tool_turn_prints_a_blank_line() -> None:
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


def test_queued_choice_owns_the_turn_display() -> None:
    call = ToolCall(
        id="1",
        name="ask_user_choice",
        input={"title": "Deploy how?", "options": ["Canary", "Rolling"]},
    )
    result = _Result(
        tool_results=[
            (
                call,
                _ToolResult(
                    {
                        "ok": True,
                        "menu": "queued",
                        "summary": "Choose your preferred deployment strategy.",
                    }
                ),
            )
        ],
        final_text="Choose your preferred deployment strategy.",
    )
    session = _Session()
    session.pending_user_choice = object()

    response_text, display_chunks, use_final_text = _compose_response(result, session, _counts(1))

    assert response_text == ""
    assert display_chunks == []
    assert use_final_text is False


def test_queued_choice_preserves_sibling_tool_results() -> None:
    github = ToolCall(id="1", name="github_cli", input={"command": "run list"})
    choice = ToolCall(
        id="2",
        name="ask_user_choice",
        input={"title": "Deploy how?", "options": ["Canary", "Rolling"]},
    )
    result = _Result(
        tool_results=[
            (github, _ToolResult(_payload("3 failed, 59 succeeded"))),
            (
                choice,
                _ToolResult(
                    {
                        "ok": True,
                        "menu": "queued",
                        "summary": "Choose your preferred deployment strategy.",
                    }
                ),
            ),
        ],
        final_text="Choose your preferred deployment strategy.",
    )
    session = _Session()
    session.pending_user_choice = object()

    response_text, display_chunks, use_final_text = _compose_response(result, session, _counts(2))

    assert response_text == "3 failed, 59 succeeded"
    assert display_chunks == ["3 failed, 59 succeeded"]
    assert use_final_text is False


def test_queued_choice_preserves_substantive_closing_text() -> None:
    choice = ToolCall(
        id="1",
        name="ask_user_choice",
        input={"title": "Deploy how?", "options": ["Canary", "Rolling"]},
    )
    closing = "Choose Canary only if the error rate remains stable."
    result = _Result(
        tool_results=[(choice, _ToolResult({"ok": True, "menu": "queued"}))],
        final_text=closing,
    )
    session = _Session()
    session.pending_user_choice = object()

    response_text, display_chunks, use_final_text = _compose_response(result, session, _counts(1))

    assert response_text == closing
    assert display_chunks == [closing]
    assert use_final_text is True


def test_queued_choice_preserves_recommendation_using_picker_words() -> None:
    choice = ToolCall(
        id="1",
        name="ask_user_choice",
        input={
            "title": "Choose a deployment environment: staging or production",
            "options": ["Staging", "Production"],
        },
    )
    closing = "Choose staging."
    result = _Result(
        tool_results=[(choice, _ToolResult({"ok": True, "menu": "queued"}))],
        final_text=closing,
    )
    session = _Session()
    session.pending_user_choice = object()

    response_text, display_chunks, use_final_text = _compose_response(result, session, _counts(1))

    assert response_text == closing
    assert display_chunks == [closing]
    assert use_final_text is True


def test_choice_failure_remains_visible_with_model_closing() -> None:
    choice = ToolCall(id="1", name="ask_user_choice", input={"title": "", "options": []})
    result = _Result(
        tool_results=[
            (
                choice,
                _ToolResult(
                    {"ok": False, "error": "title is required"},
                    is_error=True,
                ),
            )
        ],
        final_text="I could not open the picker.",
    )

    response_text, display_chunks, use_final_text = _compose_response(
        result, _Session(), _counts(1)
    )

    assert response_text == "I could not open the picker.\ntitle is required"
    assert display_chunks == ["I could not open the picker.", "title is required"]
    assert use_final_text is True


def test_choice_failure_closing_preserves_self_recording_sibling_history() -> None:
    slash = ToolCall(id="1", name="slash_invoke", input={"command": "/health"})
    choice = ToolCall(id="2", name="ask_user_choice", input={"title": "", "options": []})
    result = _Result(
        tool_results=[
            (slash, _ToolResult({"ok": True})),
            (
                choice,
                _ToolResult(
                    {"ok": False, "error": "title is required"},
                    is_error=True,
                ),
            ),
        ],
        final_text="I could not open the picker.",
    )
    counts = _counts(
        2,
        executed_entries=[
            {
                "type": "slash",
                "text": "/health",
                "ok": True,
                "response_text": "Health check: degraded",
            }
        ],
    )

    response_text, display_chunks, use_final_text = _compose_response(result, _Session(), counts)

    assert response_text == (
        "Health check: degraded\nI could not open the picker.\ntitle is required"
    )
    assert display_chunks == ["I could not open the picker.", "title is required"]
    assert use_final_text is True


def test_choice_failure_remains_visible_beside_preferred_sibling_response() -> None:
    github = ToolCall(id="1", name="github_cli", input={"command": "run list"})
    choice = ToolCall(id="2", name="ask_user_choice", input={"title": "", "options": []})
    result = _Result(
        tool_results=[
            (github, _ToolResult(_payload("3 failed, 59 succeeded"))),
            (
                choice,
                _ToolResult(
                    {"ok": False, "error": "title is required"},
                    is_error=True,
                ),
            ),
        ],
        final_text="I could not open the picker.",
    )

    response_text, display_chunks, use_final_text = _compose_response(
        result, _Session(), _counts(2)
    )

    assert "3 failed, 59 succeeded" in response_text
    assert "title is required" in response_text
    assert display_chunks == ["3 failed, 59 succeeded\ntitle is required"]
    assert use_final_text is False


def test_selected_choice_hides_only_a_pure_acknowledgement() -> None:
    result = _Result(tool_results=[], final_text="Blue-green selected.")
    session = _Session()
    session.terminal.pending_choice_response = "Blue-green"

    response_text, display_chunks, use_final_text = _compose_response(result, session, _counts(0))

    assert response_text == ""
    assert display_chunks == []
    assert use_final_text is False
    assert session.terminal.pending_choice_response is None


def test_selected_choice_keeps_meaningful_follow_up_response() -> None:
    result = _Result(
        tool_results=[],
        final_text="Blue-green avoids routing a full release to every instance at once.",
    )
    session = _Session()
    session.terminal.pending_choice_response = "Blue-green"

    _response_text, display_chunks, _use_final_text = _compose_response(result, session, _counts(0))

    shown = "\n".join(display_chunks)
    assert "Blue-green avoids routing" in shown
    assert session.terminal.pending_choice_response is None


def test_bulky_tool_output_is_capped_and_fenced_for_display() -> None:
    # A large tool result must not flood the transcript or blend into the report:
    # it is capped and shown in its own fenced code block for the console.
    github = ToolCall(id="1", name="github_cli", input={"command": "run list"})
    bulky = "\n".join(f"run {i} failure 2026-08-01T09:11:00Z" for i in range(30))
    result = _Result(
        tool_results=[(github, _ToolResult(_payload(bulky)))],
        final_text="Here is the run history.",
    )

    response_text, display_chunks, _use_final = _compose_response(result, _Session(), _counts(1))
    joined = "\n".join(display_chunks)

    # Display: capped + fenced. Model/history: full, unfenced.
    assert "```text" in joined
    assert "… (output truncated)" in joined
    assert joined.count("run ") <= 12
    assert "```text" not in response_text
    assert response_text.count("run ") == 30
