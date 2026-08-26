"""Tests for the blocking human hand-off choice tool."""

from __future__ import annotations

import io

from rich.console import Console

from core.agent_harness.human_interaction import UserChoiceRequest
from core.agent_harness.tools.tool_context import ActionToolScope
from tools.interactive_shell.actions.ask_choice import (
    ask_user_choice_tool,
    execute_ask_user_choice_tool,
)

_QUESTION = {
    "id": "uncommitted_changes",
    "header": "Worktree",
    "question": "How should I handle the uncommitted changes?",
    "options": [
        {
            "label": "Stash (Recommended)",
            "description": "Temporarily save all local changes.",
        },
        {
            "label": "Commit changes",
            "description": "Create a local work-in-progress commit.",
        },
        {
            "label": "Use worktree",
            "description": "Keep this checkout untouched.",
        },
    ],
}
_QUESTIONS = [_QUESTION]


class _HumanInteraction:
    def __init__(self, answer: str | None) -> None:
        self.answer = answer
        self.requests: list[UserChoiceRequest] = []

    def choose(self, request: UserChoiceRequest) -> str | None:
        self.requests.append(request)
        return self.answer


def _ctx(
    *,
    human_interaction: _HumanInteraction | None = None,
) -> ActionToolScope:
    console = Console(file=io.StringIO(), force_terminal=False, highlight=False)
    return ActionToolScope(
        session=object(),
        console=console,
        human_interaction=human_interaction,
    )


def test_ask_user_choice_tool_is_action_surface_read_only() -> None:
    assert ask_user_choice_tool.name == "ask_user_choice"
    assert "action" in ask_user_choice_tool.surfaces
    assert ask_user_choice_tool.side_effect_level == "read_only"
    assert ask_user_choice_tool.parallel_safe is False
    questions_schema = ask_user_choice_tool.input_schema["properties"]["questions"]
    assert questions_schema["maxItems"] == 1
    assert questions_schema["items"]["properties"]["options"]["maxItems"] == 3


def test_choice_answer_returns_to_the_same_tool_call() -> None:
    interaction = _HumanInteraction("Commit changes")
    ctx = _ctx(human_interaction=interaction)

    result = execute_ask_user_choice_tool({"questions": _QUESTIONS}, ctx)

    assert result == {
        "ok": True,
        "answers": {"uncommitted_changes": {"answers": ["Commit changes"]}},
        "summary": "Worktree: Commit changes",
    }
    request = interaction.requests[0]
    assert request.question == _QUESTION["question"]
    assert tuple(option.label for option in request.options) == (
        "Stash (Recommended)",
        "Commit changes",
        "Use worktree",
    )


def test_missing_human_interaction_port_falls_back() -> None:
    ctx = _ctx()
    result = execute_ask_user_choice_tool({"questions": _QUESTIONS}, ctx)

    assert result["ok"] is True
    assert result["menu"] == "unavailable"


def test_cancelled_choice_is_reported() -> None:
    ctx = _ctx(human_interaction=_HumanInteraction(None))

    result = execute_ask_user_choice_tool({"questions": _QUESTIONS}, ctx)

    assert result == {"ok": False, "cancelled": True}


def test_missing_question_is_rejected() -> None:
    result = execute_ask_user_choice_tool({"questions": []}, _ctx())
    assert result["ok"] is False


def test_fewer_than_two_distinct_options_is_rejected() -> None:
    ctx = _ctx()

    question = {
        **_QUESTION,
        "options": [
            {"label": "Stash", "description": "Save changes."},
            {"label": "Stash", "description": "Save changes."},
        ],
    }
    result = execute_ask_user_choice_tool({"questions": [question]}, ctx)

    assert result["ok"] is False


def test_too_many_options_is_rejected() -> None:
    options = [
        {"label": f"Option {index}", "description": f"Choose option {index}."} for index in range(4)
    ]
    result = execute_ask_user_choice_tool(
        {"questions": [{**_QUESTION, "options": options}]},
        _ctx(),
    )
    assert result["ok"] is False
