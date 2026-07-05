from __future__ import annotations

import pytest

from core.state import (
    MAX_CONVERSATION_MESSAGES,
    AgentStateError,
    MutableAgentState,
)
from core.types import AgentTool


def _tool(name: str) -> AgentTool:
    return AgentTool(
        name=name,
        description=f"{name} tool",
        input_schema={"type": "object", "properties": {}, "additionalProperties": False},
        execute=lambda _payload, _ctx: {"ok": True},
    )


def test_actions_validate_mutate_and_notify() -> None:
    state = MutableAgentState()
    changes: list[str] = []
    state.subscribe(lambda change: changes.append(change.action))

    state.set_system_prompt("system")
    state.set_model(provider="openai", name="gpt-5")
    state.set_thinking_level("medium")

    snapshot = state.snapshot()
    assert snapshot.system_prompt == "system"
    assert snapshot.model.provider == "openai"
    assert snapshot.model.name == "gpt-5"
    assert snapshot.model.thinking_level == "medium"
    assert changes == ["system_prompt_update", "model_update", "thinking_level_update"]


def test_available_active_tool_invariants() -> None:
    first = _tool("first")
    duplicate = _tool("first")
    second = _tool("second")

    with pytest.raises(AgentStateError, match="duplicate tool"):
        MutableAgentState(available_tools=[first, duplicate])

    state = MutableAgentState(available_tools=[first])

    with pytest.raises(AgentStateError, match="not available"):
        state.set_active_tools([second])


def test_message_cap_and_snapshot_copy() -> None:
    state = MutableAgentState()

    for index in range(MAX_CONVERSATION_MESSAGES + 3):
        state.record_turn(f"user {index}", f"assistant {index}")

    snapshot = state.snapshot()
    assert len(snapshot.messages) == MAX_CONVERSATION_MESSAGES
    assert snapshot.messages[0] == ("user", "user 15")

    state.messages.append(("user", "live mutation"))
    assert snapshot.messages[-1] != ("user", "live mutation")


def test_run_status_and_pending_tool_invariants() -> None:
    tool = _tool("inspect")
    state = MutableAgentState(available_tools=[tool], active_tools=[tool])

    state.begin_run()
    with pytest.raises(AgentStateError, match="already active"):
        state.begin_run()

    state.mark_tool_pending("inspect")
    assert state.snapshot().pending_tool_calls == frozenset({"inspect"})

    state.clear_tool_pending("inspect")
    state.end_run()
    assert state.run_status == "idle"

    with pytest.raises(AgentStateError, match="not active"):
        state.end_run()


def test_select_turn_runtime_input_excludes_shell_metadata() -> None:
    tool = _tool("inspect")
    state = MutableAgentState(
        system_prompt="system",
        available_tools=[tool],
        active_tools=[tool],
        resolved_integrations={"sentry": {"dsn": "redacted"}},
        max_iterations=3,
    )
    state.record_turn("u", "a")

    selected = state.select_turn_runtime_input("next")

    assert selected.text == "next"
    assert selected.system_prompt == "system"
    assert selected.active_tools == (tool,)
    assert selected.resolved_integrations == {"sentry": {"dsn": "redacted"}}
    assert selected.max_iterations == 3
    assert not hasattr(selected, "terminal_capabilities")
