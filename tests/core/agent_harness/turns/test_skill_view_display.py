"""Loading a skill must not print the skill body.

``skill_view`` returns the full recipe so the *model* can follow it. With no
``summary`` in the result, the generic formatter fell through to dumping
``skill_view input: {...}`` plus the entire 5k-character body — escaped JSON,
box-drawing characters and all — onto the user's screen before the briefing.
"""

from __future__ import annotations

from core.agent_harness.turns.action_driver import _format_generic_tool_payload
from core.llm.types import ToolCall
from tools.interactive_shell.actions.skill_view import execute_skill_view_tool


class _NoContext:
    pass


def _skill_view_result() -> dict[str, object]:
    return execute_skill_view_tool({"name": "morning-report"}, _NoContext())


def test_loading_a_skill_reports_a_line_not_the_body() -> None:
    """The user sees that a skill loaded, never its contents."""
    # Arrange
    import json

    result = _skill_view_result()
    assert result["ok"] is True, result

    class _Result:
        content = json.dumps(result)
        details = result

    # Act
    shown = _format_generic_tool_payload(
        ToolCall(id="t1", name="skill_view", input={"name": "morning-report"}), _Result()
    )

    # Assert
    assert "morning-report" in shown
    assert "CDATA" not in shown, "the skill body reached the user"
    assert "shell_run(command=" not in shown
    assert len(shown) < 200, f"expected a short line, got {len(shown)} chars"
