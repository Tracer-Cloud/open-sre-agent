"""Regression: observation stashing is tag-driven, not vendor-name hardcoding."""

from __future__ import annotations

from types import SimpleNamespace

from core.agent_harness.turns.action_driver import _should_stash_observation
from core.llm.types import ToolCall
from core.tool_framework.tags import SUMMARIZE_OBSERVATION_TAG


def _result(*names: str, error: bool = False) -> SimpleNamespace:
    pairs = []
    for name in names:
        pairs.append(
            (
                ToolCall(id=f"id-{name}", name=name, input={}),
                SimpleNamespace(is_error=error, content='{"ok": true}'),
            )
        )
    return SimpleNamespace(tool_results=pairs)


def _tools(*names: str, tagged: bool = True) -> dict[str, SimpleNamespace]:
    tags = (SUMMARIZE_OBSERVATION_TAG,) if tagged else ()
    return {name: SimpleNamespace(name=name, tags=tags) for name in names}


def test_should_stash_observation_for_tagged_discovery_tools() -> None:
    tools = _tools(
        "slack_list_team_members",
        "slack_read_messages",
        "slack_search_messages",
    )
    assert (
        _should_stash_observation(_result("slack_list_team_members"), tools_by_name=tools) is True
    )
    assert _should_stash_observation(_result("slack_read_messages"), tools_by_name=tools) is True
    assert _should_stash_observation(_result("slack_search_messages"), tools_by_name=tools) is True


def test_should_not_stash_observation_for_untagged_action_tools() -> None:
    tools = {
        **_tools("parity_probe", tagged=False),
        **_tools("slack_reply_message", tagged=False),
        **_tools("slack_send_message", tagged=False),
        **_tools("slack_list_team_members", tagged=True),
    }
    assert _should_stash_observation(_result("parity_probe"), tools_by_name=tools) is False
    assert _should_stash_observation(_result("slack_reply_message"), tools_by_name=tools) is False
    assert _should_stash_observation(_result("slack_send_message"), tools_by_name=tools) is False
    assert (
        _should_stash_observation(
            _result("slack_list_team_members", error=True),
            tools_by_name=tools,
        )
        is False
    )
