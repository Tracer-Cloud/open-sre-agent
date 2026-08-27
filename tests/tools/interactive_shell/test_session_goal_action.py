from __future__ import annotations

from core.agent_harness.session_goal.goal import SessionGoal, attach_session_goal
from core.agent_harness.tools import ActionToolScope
from core.agent_harness.turns.headless_adapters import InMemorySessionState
from tools.interactive_shell.actions.session_goal import execute_session_goal_tool


def test_session_goal_tool_attaches_structured_checklist() -> None:
    session = InMemorySessionState()

    result = execute_session_goal_tool(
        {
            "condition": "Complete the walkthrough",
            "items": ["First", "Second"],
        },
        ActionToolScope(session=session, console=object()),
    )

    assert result["attached"] is True
    assert session.session_goal is not None
    assert session.session_goal.condition == "Complete the walkthrough"
    assert session.session_goal.checklist == ("First", "Second")


def test_session_goal_tool_does_not_replace_attached_goal() -> None:
    session = InMemorySessionState()
    original = attach_session_goal(session, SessionGoal(condition="Existing"))

    result = execute_session_goal_tool(
        {"condition": "Replacement"},
        ActionToolScope(session=session, console=object()),
    )

    assert result["attached"] is False
    assert session.session_goal is original
