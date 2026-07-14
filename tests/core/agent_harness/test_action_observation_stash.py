"""Regression: observation stashing stays limited to Slack discovery tools."""

from __future__ import annotations

from types import SimpleNamespace

from core.agent_harness.turns.action_driver import _should_stash_observation
from core.llm.types import ToolCall


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


def test_should_stash_observation_for_slack_discovery_tools() -> None:
    assert _should_stash_observation(_result("slack_list_team_members")) is True
    assert _should_stash_observation(_result("slack_read_messages")) is True
    assert _should_stash_observation(_result("slack_search_messages")) is True


def test_should_not_stash_observation_for_generic_action_tools() -> None:
    assert _should_stash_observation(_result("parity_probe")) is False
    assert _should_stash_observation(_result("slack_reply_message")) is False
    assert _should_stash_observation(_result("slack_send_message")) is False
    assert _should_stash_observation(_result("slack_list_team_members", error=True)) is False
