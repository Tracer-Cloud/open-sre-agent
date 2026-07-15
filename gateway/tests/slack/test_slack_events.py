from __future__ import annotations

from typing import Any

from gateway.slack.events import parse_events_api_payload


def _mention_payload(**event_overrides: Any) -> dict[str, Any]:
    event = {
        "type": "app_mention",
        "user": "U111",
        "channel": "C222",
        "ts": "1700000000.000100",
        "text": "<@UBOT> check the checkout service",
    }
    event.update(event_overrides)
    return {"team_id": "T333", "event": event}


def test_parses_app_mention_and_strips_leading_bot_mention() -> None:
    inbound = parse_events_api_payload(_mention_payload())

    assert inbound is not None
    assert inbound.team_id == "T333"
    assert inbound.user_id == "U111"
    assert inbound.channel_id == "C222"
    assert inbound.text == "check the checkout service"
    assert inbound.thread_ts == "1700000000.000100"


def test_mention_inside_existing_thread_keeps_parent_thread_ts() -> None:
    inbound = parse_events_api_payload(_mention_payload(thread_ts="1699999999.000001"))

    assert inbound is not None
    assert inbound.thread_ts == "1699999999.000001"
    assert inbound.ts == "1700000000.000100"


def test_conversation_key_combines_team_channel_and_thread() -> None:
    inbound = parse_events_api_payload(_mention_payload())

    assert inbound is not None
    assert inbound.conversation_key == "T333:C222:1700000000.000100"


def test_identical_conversations_in_different_teams_stay_isolated() -> None:
    team_a = parse_events_api_payload(_mention_payload())
    payload_b = _mention_payload()
    payload_b["team_id"] = "T999"
    team_b = parse_events_api_payload(payload_b)

    assert team_a is not None and team_b is not None
    # Same channel and thread ids, different workspaces: separate sessions/memory.
    assert team_a.conversation_key != team_b.conversation_key


def test_parses_direct_message() -> None:
    inbound = parse_events_api_payload(
        {
            "team_id": "T333",
            "event": {
                "type": "message",
                "channel_type": "im",
                "user": "U111",
                "channel": "D444",
                "ts": "1700000000.000200",
                "text": "what integrations do I have?",
            },
        }
    )

    assert inbound is not None
    assert inbound.channel_id == "D444"
    assert inbound.text == "what integrations do I have?"


def test_rejects_bot_echo_and_message_subtypes() -> None:
    assert parse_events_api_payload(_mention_payload(bot_id="B555")) is None
    assert parse_events_api_payload(_mention_payload(subtype="message_changed")) is None


def test_accepts_file_share_and_thread_broadcast_subtypes() -> None:
    # These subtypes still carry a real user mention and must be answered,
    # not silently dropped like edit/join bookkeeping subtypes.
    for subtype in ("file_share", "thread_broadcast"):
        inbound = parse_events_api_payload(_mention_payload(subtype=subtype))
        assert inbound is not None, f"{subtype} mention was dropped"
        assert inbound.text == "check the checkout service"


def test_rejects_channel_messages_without_mention() -> None:
    payload = _mention_payload(type="message", channel_type="channel")
    assert parse_events_api_payload(payload) is None


def test_rejects_payloads_missing_required_fields() -> None:
    assert parse_events_api_payload({}) is None
    assert parse_events_api_payload(_mention_payload(text="")) is None
    assert parse_events_api_payload(_mention_payload(user="")) is None
    assert parse_events_api_payload(_mention_payload(text="<@UBOT>")) is None
