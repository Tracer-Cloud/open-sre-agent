from __future__ import annotations

from core.state import MAX_CONVERSATION_MESSAGES, MutableAgentState


def test_record_turn_appends_transcript() -> None:
    state = MutableAgentState()
    state.record_turn("hello", "hi there")
    assert state.messages == [("user", "hello"), ("assistant", "hi there")]


def test_message_cap_trims_oldest() -> None:
    state = MutableAgentState()
    for index in range(MAX_CONVERSATION_MESSAGES + 3):
        state.record_turn(f"user {index}", f"assistant {index}")

    assert len(state.messages) == MAX_CONVERSATION_MESSAGES
    assert state.messages[0] == ("user", "user 15")


def test_messages_setter_replaces_and_trims() -> None:
    state = MutableAgentState()
    state.messages = [("user", str(i)) for i in range(MAX_CONVERSATION_MESSAGES + 5)]
    assert len(state.messages) == MAX_CONVERSATION_MESSAGES


def test_last_observation_roundtrip_and_reset() -> None:
    state = MutableAgentState()
    assert state.last_observation is None

    state.last_observation = "db saturation"
    assert state.last_observation == "db saturation"

    state.reset_observation()
    assert state.last_observation is None


def test_clear_empties_transcript_and_observation() -> None:
    state = MutableAgentState()
    state.record_turn("u", "a")
    state.last_observation = "obs"

    state.clear()

    assert state.messages == []
    assert state.last_observation is None
