"""Tests for work-item reminder message builders."""

from __future__ import annotations

from core.domain.work_items import (
    build_work_item_checkin_message,
    build_work_item_reminder_message,
    make_work_item,
)


def test_reminder_message_includes_completion_hint() -> None:
    item = make_work_item(title="Ping demo owners", priority="high", project="hackathon")

    message = build_work_item_reminder_message(item)

    assert "Ping demo owners" in message
    assert item.display_id in message
    assert "/work done" in message


def test_checkin_message_is_empty_without_open_work() -> None:
    assert build_work_item_checkin_message([]) == ""


def test_checkin_message_ranks_open_work() -> None:
    low = make_work_item(title="Optional polish", priority="low")
    urgent = make_work_item(title="Demo blocker", priority="urgent")

    message = build_work_item_checkin_message([low, urgent], project="hackathon")

    assert message.index("Demo blocker") < message.index("Optional polish")
    assert "OpenSRE work check-in for hackathon" in message
