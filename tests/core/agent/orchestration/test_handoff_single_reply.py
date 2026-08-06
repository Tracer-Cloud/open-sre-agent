"""A handed-off turn speaks once.

``assistant_handoff`` means "the assistant will answer this" — the action's own
closing prose is a second reply the user did not ask for. Reproduces the
double "good morning" seen in the interactive shell.
"""

from __future__ import annotations

from typing import Any

from core.agent_harness.turns.action_driver import _compose_response, _TurnCounts

_ACTION_CLOSING = "Good morning, Yauhen! How can I help you today?"


class _Call:
    def __init__(self, name: str, tool_input: dict[str, Any]) -> None:
        self.name = name
        self.input = tool_input


class _Result:
    """Minimal stand-in for the tool-calling turn result."""

    def __init__(self, executed: list[tuple[_Call, Any]], final_text: str) -> None:
        self.executed = executed
        self.final_text = final_text
        self.planned = [call for call, _ in executed]


class _Session:
    history: list[dict[str, Any]] = []


def _counts(handoff: tuple[str, ...]) -> _TurnCounts:
    return _TurnCounts(
        executed_entries=[],
        executed_count=1,
        executed_success_count=1,
        generic_success_count=0,
        planned_count=1,
        handled=True,
        investigation_dispatched=False,
        handoff_contents=handoff,
    )


def test_handoff_turn_does_not_also_speak_the_action_closing() -> None:
    # Arrange: the action called assistant_handoff and still produced a chatty
    # closing. The router sends this turn on to the LLM, which answers properly.
    call = _Call("assistant_handoff", {"content": "greeting"})
    result = _Result([(call, {"ok": True})], _ACTION_CLOSING)

    # Act
    _response_text, display_chunks, _use_final_text = _compose_response(
        result, _Session(), _counts(("greeting",))
    )

    # Assert: display_chunks is what reaches the console. Leaving the closing
    # here prints a greeting the LLM is about to print again, so the user reads
    # two replies to one message.
    assert _ACTION_CLOSING not in "\n".join(display_chunks)


def test_non_handoff_turn_still_speaks_its_closing() -> None:
    # Arrange: a turn the action fully handled. Nothing follows it, so its
    # closing text is the only reply the user gets.
    closing = "Done — removed 3 stale branches."
    call = _Call("some_registry_tool", {})
    result = _Result([(call, {"ok": True})], closing)

    # Act
    _response_text, display_chunks, _use_final_text = _compose_response(
        result, _Session(), _counts(())
    )

    # Assert: suppressing here would leave the turn silent.
    assert closing in "\n".join(display_chunks)
