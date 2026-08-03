from __future__ import annotations

from integrations.vercel.client import (
    _extract_event_text,
    _extract_runtime_log_message,
)


def test_extract_event_text_prefers_text_field() -> None:
    event = {"text": "build started", "payload": {"text": "ignored"}}

    assert _extract_event_text(event) == "build started"


def test_extract_event_text_uses_payload_text_when_text_missing() -> None:
    event = {"payload": {"text": "payload text available"}}

    assert _extract_event_text(event) == "payload text available"


def test_extract_event_text_returns_empty_string_for_missing_text() -> None:
    assert _extract_event_text({}) == ""
    assert _extract_event_text({"payload": {}}) == ""
    assert _extract_event_text({"payload": None}) == ""
    assert _extract_event_text({"payload": "abc"}) == ""


def test_extract_event_text_returns_empty_string_for_empty_text_values() -> None:
    assert _extract_event_text({"text": ""}) == ""
    assert _extract_event_text({"payload": {"text": ""}}) == ""


def test_extract_runtime_log_message_prefers_message_field() -> None:
    log = {"message": "runtime error", "payload": {"text": "ignored"}}

    assert _extract_runtime_log_message(log) == "runtime error"


def test_extract_runtime_log_message_falls_back_to_nested_payload_text_message_or_body() -> None:
    assert _extract_runtime_log_message({"payload": {"text": "payload text"}}) == "payload text"
    assert (
        _extract_runtime_log_message({"payload": {"message": "payload message"}})
        == "payload message"
    )
    assert _extract_runtime_log_message({"payload": {"body": "payload body"}}) == "payload body"


def test_extract_runtime_log_message_returns_payload_string_when_payload_is_not_dict() -> None:
    assert _extract_runtime_log_message({"payload": "unexpected payload"}) == "unexpected payload"


def test_extract_runtime_log_message_returns_empty_string_for_missing_message() -> None:
    assert _extract_runtime_log_message({}) == ""
    assert _extract_runtime_log_message({"payload": {}}) == ""
    assert _extract_runtime_log_message({"message": None, "payload": {}}) == ""


def test_extract_runtime_log_message_returns_empty_string_for_empty_message_values() -> None:
    assert _extract_runtime_log_message({"message": ""}) == ""
    assert _extract_runtime_log_message({"payload": {"text": ""}}) == ""
    assert _extract_runtime_log_message({"payload": {"message": ""}}) == ""
    assert _extract_runtime_log_message({"payload": {"body": ""}}) == ""


def test_extract_runtime_log_message_prefers_payload_text_over_other_payload_fields() -> None:
    log = {
        "payload": {
            "text": "A",
            "message": "B",
            "body": "C",
        }
    }
    assert _extract_runtime_log_message(log) == "A"
