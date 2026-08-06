"""``AgentMode`` StrEnum pins and state dump round-trip."""

from __future__ import annotations

from core.state import AgentMode, AgentStateModel, make_chat_state
from tools.investigation.state_factory import make_initial_state


def test_agent_mode_members_are_byte_identical() -> None:
    assert set(AgentMode) == {
        AgentMode.CHAT,
        AgentMode.INVESTIGATION,
        AgentMode.AGENT_INCIDENT,
    }
    assert AgentMode.CHAT == "chat"
    assert AgentMode.INVESTIGATION == "investigation"
    assert AgentMode.AGENT_INCIDENT == "agent_incident"
    assert AgentMode("chat") is AgentMode.CHAT


def test_agent_state_model_accepts_string_and_dumps_string() -> None:
    model = AgentStateModel.model_validate({"mode": "investigation"})
    assert model.mode is AgentMode.INVESTIGATION
    dumped = model.model_dump(mode="json")
    assert dumped["mode"] == "investigation"
    assert type(dumped["mode"]) is str


def test_make_chat_state_mode_is_plain_string() -> None:
    state = make_chat_state()
    assert state["mode"] == "chat"
    assert state["mode"] == AgentMode.CHAT
    # ``mode="json"`` dump must not leave a StrEnum member in the runtime dict.
    assert type(state["mode"]) is str


def test_make_initial_state_mode_is_plain_string() -> None:
    state = make_initial_state(raw_alert={"source": "grafana"})
    assert state["mode"] == "investigation"
    assert type(state["mode"]) is str
