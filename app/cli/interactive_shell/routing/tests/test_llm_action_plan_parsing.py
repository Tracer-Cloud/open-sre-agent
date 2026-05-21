"""Unit tests for LLM tool-plan parsing guards."""

from __future__ import annotations

import json

from app.cli.interactive_shell.routing.handle_message_with_agent.orchestration.llm_action_planner.parsing import (
    _parse_tool_plan,
)
from app.cli.interactive_shell.runtime.session import ReplSession


def test_parse_tool_plan_drops_unavailable_tool_calls() -> None:
    session = ReplSession(available_capabilities={"shell_commands": ()})
    raw = json.dumps(
        {
            "tool_calls": [
                {
                    "name": "shell_run",
                    "arguments": {"command": "pwd"},
                }
            ],
            "text": "",
        }
    )
    parsed = _parse_tool_plan(raw, session=session)
    assert parsed is not None
    actions, has_unhandled = parsed
    assert actions == []
    assert has_unhandled is True


def test_parse_tool_plan_keeps_available_tool_calls() -> None:
    session = ReplSession(available_capabilities={"shell_commands": ("pwd",)})
    raw = json.dumps(
        {
            "tool_calls": [
                {
                    "name": "shell_run",
                    "arguments": {"command": "pwd"},
                }
            ],
            "text": "",
        }
    )
    parsed = _parse_tool_plan(raw, session=session)
    assert parsed is not None
    actions, has_unhandled = parsed
    assert len(actions) == 1
    assert actions[0].kind == "shell"
    assert actions[0].content == "pwd"
    assert has_unhandled is False
