"""Tests for pipeline state update reducers."""

from __future__ import annotations

from core.state import AgentState
from core.state.updates import apply_state_updates


def test_apply_state_updates_last_write_wins() -> None:
    state: AgentState = {"severity": "before"}
    apply_state_updates(state, {"severity": "after", "root_cause": "oom"})
    assert state["severity"] == "after"
    assert state["root_cause"] == "oom"


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
    state: AgentState = {"severity": "unchanged"}
    apply_state_updates(state, {})
    apply_state_updates(state, None)
    assert state == {"severity": "unchanged"}
