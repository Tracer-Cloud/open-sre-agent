"""Reply feedback: block shape and click recording."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from gateway.slack.feedback import (
    FEEDBACK_ACTION_ID,
    feedback_block,
    record_feedback_payload,
)


def _click_payload(*, action_id: str = FEEDBACK_ACTION_ID, value: str = "good") -> dict[str, Any]:
    return {
        "type": "block_actions",
        "user": {"id": "U1"},
        "channel": {"id": "C1"},
        "message": {"ts": "1700.42", "text": "the secret answer body"},
        "actions": [{"action_id": action_id, "value": value}],
    }


def test_feedback_block_shape() -> None:
    block = feedback_block()

    assert block["type"] == "context_actions"
    element = block["elements"][0]
    assert element["type"] == "feedback_buttons"
    assert element["action_id"] == FEEDBACK_ACTION_ID
    assert element["positive_button"]["value"] == "good"
    assert element["negative_button"]["value"] == "bad"


def test_click_is_recorded_as_jsonl(tmp_path: Path) -> None:
    log = tmp_path / "feedback.jsonl"

    assert record_feedback_payload(_click_payload(value="bad"), path=log) is True

    entry = json.loads(log.read_text().strip())
    assert entry["verdict"] == "bad"
    assert entry["user_id"] == "U1"
    assert entry["channel_id"] == "C1"
    assert entry["message_ts"] == "1700.42"


def test_recorded_entry_carries_no_message_content(tmp_path: Path) -> None:
    # Slack's data-retention guidance: store metadata, not message data.
    log = tmp_path / "feedback.jsonl"

    record_feedback_payload(_click_payload(), path=log)

    assert "secret answer body" not in log.read_text()


def test_other_action_ids_are_ignored(tmp_path: Path) -> None:
    log = tmp_path / "feedback.jsonl"

    payload = _click_payload(action_id="opensre_approval_approve")
    assert record_feedback_payload(payload, path=log) is False
    assert not log.exists()


def test_non_block_actions_payloads_are_ignored(tmp_path: Path) -> None:
    log = tmp_path / "feedback.jsonl"

    assert record_feedback_payload({"type": "view_submission"}, path=log) is False
    assert not log.exists()


def test_multiple_clicks_append(tmp_path: Path) -> None:
    log = tmp_path / "feedback.jsonl"

    record_feedback_payload(_click_payload(value="good"), path=log)
    record_feedback_payload(_click_payload(value="bad"), path=log)

    lines = [json.loads(line) for line in log.read_text().splitlines()]
    assert [entry["verdict"] for entry in lines] == ["good", "bad"]
