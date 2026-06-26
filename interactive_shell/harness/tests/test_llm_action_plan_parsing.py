"""Unit tests for LLM tool-plan parsing guards."""

from __future__ import annotations

import json

from interactive_shell.harness.orchestration.llm_action_planner.parsing import (
    _parse_tool_plan,
)
from interactive_shell.runtime.core.session import ReplSession


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
    # Unavailable tool calls are silently dropped; v0.1 never marks the turn
    # unhandled (no planning-stage fail-closed). The clause falls through to chat.
    assert actions == []
    assert has_unhandled is False


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


def test_parse_tool_plan_keeps_verify_for_unconfigured_service() -> None:
    session = ReplSession()
    session.configured_integrations_known = True
    session.configured_integrations = ()
    raw = json.dumps(
        {
            "tool_calls": [
                {
                    "name": "slash_invoke",
                    "arguments": {
                        "command": "/integrations",
                        "args": ["verify", "sentry"],
                    },
                }
            ],
            "text": "",
        }
    )

    parsed = _parse_tool_plan(raw, session=session)

    assert parsed is not None
    actions, has_unhandled = parsed
    assert len(actions) == 1
    assert actions[0].kind == "slash"
    assert actions[0].content == "/integrations verify sentry"
    assert actions[0].args == {"command": "/integrations", "args": ["verify", "sentry"]}
    assert has_unhandled is False


def test_parse_tool_plan_still_drops_show_for_unconfigured_service() -> None:
    session = ReplSession()
    session.configured_integrations_known = True
    session.configured_integrations = ()
    raw = json.dumps(
        {
            "tool_calls": [
                {
                    "name": "slash_invoke",
                    "arguments": {
                        "command": "/integrations",
                        "args": ["show", "sentry"],
                    },
                }
            ],
            "text": "",
        }
    )

    parsed = _parse_tool_plan(raw, session=session)

    assert parsed is not None
    actions, has_unhandled = parsed
    assert actions == []
    assert has_unhandled is False
