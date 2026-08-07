"""Characterization of the action turn's outcome, field by field.

``_run_action_turn`` is one long function doing three unrelated jobs: building
the agent, running it, and turning what happened into a
``ToolCallingTurnResult`` plus console output. Splitting it is only safe with
the whole observable outcome pinned first — the counts, the response text, the
handoff contents and what the user actually sees.

These assert on complete values rather than single fields, so a refactor that
drops or reorders part of the composed text fails here rather than in a
customer's terminal.
"""

from __future__ import annotations

from typing import Any

from surfaces.interactive_shell.runtime.action_turn import run_action_tool_turn
from surfaces.interactive_shell.session import Session
from tests.core.agent.orchestration.action_execution_test_harness import (
    ActionExecutionHarness,
    FakeActionLLM,
    no_tool_response,
    tool_response,
)


def _console_text(harness: ActionExecutionHarness) -> str:
    return harness.console.file.getvalue()  # type: ignore[attr-defined]


def _outcome(result: Any) -> dict[str, Any]:
    """Every field the caller can observe, as one comparable value."""
    return {
        "planned_count": result.planned_count,
        "executed_count": result.executed_count,
        "executed_success_count": result.executed_success_count,
        "has_unhandled_clause": result.has_unhandled_clause,
        "handled": result.handled,
        "handoff_contents": result.handoff_contents,
        "handoff_requires_gather": result.handoff_requires_gather,
        "accounting_status": result.accounting_status,
        "investigation_dispatched": result.investigation_dispatched,
    }


def test_a_turn_with_no_tool_calls_is_unhandled() -> None:
    """The plainest turn: the model answers without calling anything."""
    # Arrange
    harness = ActionExecutionHarness(llm=FakeActionLLM([no_tool_response("just talking")]))

    # Act
    result = run_action_tool_turn("hello", Session(), harness.console, deps=harness.deps)

    # Assert
    assert _outcome(result) == {
        "planned_count": 0,
        "executed_count": 0,
        "executed_success_count": 0,
        "has_unhandled_clause": False,
        "handled": False,
        "handoff_contents": (),
        "handoff_requires_gather": True,
        "accounting_status": "completed",
        "investigation_dispatched": False,
    }


def test_an_assistant_handoff_is_reported_but_not_counted_as_planned() -> None:
    """``assistant_handoff`` is excluded from ``planned_count`` by name.

    It is the one tool whose execution does not mean the turn did something for
    the user, so it must not flip ``handled``.
    """
    # Arrange
    harness = ActionExecutionHarness(
        llm=FakeActionLLM(
            [
                tool_response("assistant_handoff", {"content": "let me explain instead"}),
                no_tool_response(""),
            ]
        )
    )

    # Act
    result = run_action_tool_turn("why?", Session(), harness.console, deps=harness.deps)

    # Assert
    assert result.planned_count == 0
    assert result.handled is False
    assert result.handoff_contents == ("let me explain instead",)
    assert result.handoff_requires_gather is True


def test_an_answer_only_handoff_opts_out_of_the_gather_pass() -> None:
    """``requires_gather=false`` on the handoff input reaches the turn result.

    The router reads this flag to skip the live evidence-gather sweep when the
    turn's own tool work already produced everything the reply needs.
    """
    # Arrange
    harness = ActionExecutionHarness(
        llm=FakeActionLLM(
            [
                tool_response(
                    "assistant_handoff",
                    {"content": "onboarding checks all passed", "requires_gather": False},
                ),
                no_tool_response(""),
            ]
        )
    )

    # Act
    result = run_action_tool_turn(
        "onboard me on the CI/CD fix", Session(), harness.console, deps=harness.deps
    )

    # Assert
    assert result.handoff_contents == ("onboarding checks all passed",)
    assert result.handoff_requires_gather is False


def test_final_text_that_reads_like_a_reply_becomes_the_response() -> None:
    """A substantial closing message is streamed as the user-facing answer."""
    # Arrange
    report = "## Findings\n\nThe checkout service is returning 502s.\nRoot cause: bad deploy."
    harness = ActionExecutionHarness(llm=FakeActionLLM([no_tool_response(report)]))

    # Act
    result = run_action_tool_turn("what broke?", Session(), harness.console, deps=harness.deps)

    # Assert
    assert result.response_text == report


def test_a_terse_closing_line_keeps_the_tool_derived_text(monkeypatch) -> None:
    """ "done" after a tool call must not become the whole answer.

    Short one-liners are common filler. Taking one as the response would throw
    away the tool output the user actually needs — so the composed text wins
    unless the closing message reads like a real reply.
    """
    # Arrange
    from core.agent_harness.turns.action_driver import ActionTurnRunner
    from core.tool_framework.registered_tool import RegisteredTool
    from tests.core.agent.orchestration.test_agent_actions_harness import (
        _GenericActionToolProvider,
        _OutputSink,
    )

    tool = RegisteredTool(
        name="fake_send_message",
        description="Send a fake message.",
        input_schema={
            "type": "object",
            "properties": {"message": {"type": "string"}},
            "required": ["message"],
            "additionalProperties": False,
        },
        source="knowledge",
        surfaces=("action",),
        run=lambda message: {"status": "sent", "message": message},
    )
    harness = ActionExecutionHarness(
        llm=FakeActionLLM(
            [
                tool_response("fake_send_message", {"message": "hello"}),
                no_tool_response("done"),
            ]
        )
    )

    # Act
    result = ActionTurnRunner(
        output=_OutputSink(harness.console),
        tools=_GenericActionToolProvider(tool),
        deps=harness.deps,
    ).run("send it", Session())

    # Assert
    assert result.handled is True
    assert "sent" in result.response_text, result.response_text
    assert result.response_text != "done"


def test_a_terse_closing_line_is_the_answer_when_nothing_else_ran() -> None:
    """With no tool output there is nothing to preserve, so the text stands.

    Recorded because it is the mirror of the case above and easy to break when
    the composition order changes.
    """
    # Arrange
    harness = ActionExecutionHarness(llm=FakeActionLLM([no_tool_response("done")]))

    # Act
    result = run_action_tool_turn("run it", Session(), harness.console, deps=harness.deps)

    # Assert
    assert result.response_text == "done"
