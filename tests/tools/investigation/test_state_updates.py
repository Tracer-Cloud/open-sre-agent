"""Tests for pipeline state update reducers."""

from __future__ import annotations

from context.state import AgentState
from context.state.updates import apply_state_updates


def test_apply_state_updates_last_write_wins() -> None:
    state: AgentState = {"alert_name": "before"}
    apply_state_updates(state, {"alert_name": "after", "severity": "critical"})
    assert state["alert_name"] == "after"
    assert state["severity"] == "critical"


def test_apply_state_updates_appends_message_list() -> None:
    state: AgentState = {"messages": [{"role": "user", "content": "hi"}]}
    apply_state_updates(state, {"messages": [{"role": "assistant", "content": "hello"}]})
    assert state["messages"] == [
        {"role": "user", "content": "hi"},
        {"role": "assistant", "content": "hello"},
    ]


def test_apply_state_updates_appends_single_message() -> None:
    state: AgentState = {"messages": []}
    apply_state_updates(state, {"messages": {"role": "user", "content": "ping"}})
    assert state["messages"] == [{"role": "user", "content": "ping"}]


def test_apply_state_updates_noop_on_empty_updates() -> None:
    state: AgentState = {"alert_name": "unchanged"}
    apply_state_updates(state, {})
    apply_state_updates(state, None)
    assert state == {"alert_name": "unchanged"}
