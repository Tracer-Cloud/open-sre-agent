"""A closing question after a self-recording tool must reach the user.

Regression for the silent-turn failure: "remove the existing cron loops" ran a
single ``/cron list``, the model concluded with "…remove all of them?", and the
self-recording suppression dropped that question — the user saw the table and
then nothing, and a follow-up "yes" had no recorded offer to resolve against.
"""

from __future__ import annotations

from core.agent_harness.turns.action_driver import _compose_response, _TurnCounts
from core.llm.types import ToolCall
from surfaces.interactive_shell.session import Session


class _SlashResult:
    """Tool result shaped like a slash_invoke observation (no response_text)."""

    content = '{"ok": true, "output": "slash /cron list (succeeded)"}'
    details = {"ok": True, "output": "slash /cron list (succeeded)"}
    is_error = False


class _RunResult:
    def __init__(self, final_text: str) -> None:
        self.final_text = final_text
        self.tool_results = [
            (
                ToolCall(id="tc0", name="slash_invoke", input={"command": "/cron"}),
                _SlashResult(),
            )
        ]


def _counts() -> _TurnCounts:
    return _TurnCounts(
        executed_entries=[
            {
                "type": "slash",
                "text": "/cron list",
                "ok": True,
                "response_text": "slash /cron list (succeeded)",
            }
        ],
        executed_count=1,
        executed_success_count=1,
        generic_success_count=0,
        planned_count=1,
        handled=True,
        investigation_dispatched=False,
        handoff_contents=(),
    )


def _compose(final_text: str) -> tuple[str, list[str], bool]:
    return _compose_response(_RunResult(final_text), Session(), _counts())


def test_closing_question_survives_single_slash_step() -> None:
    question = "I found 5 scheduled cron loops. Do you want me to remove all of them?"

    response_text, display_chunks, use_final_text = _compose(question)

    assert use_final_text is True
    assert response_text == question
    assert question in display_chunks


def test_closing_statement_is_still_suppressed_after_single_slash_step() -> None:
    statement = "The health check passed and everything looks green across the board."

    response_text, display_chunks, use_final_text = _compose(statement)

    assert use_final_text is False
    assert statement not in response_text
    assert all(statement not in chunk for chunk in display_chunks)
