"""Tests for work-item prioritization."""

from __future__ import annotations

from datetime import UTC, datetime

from core.domain.work_items import make_work_item, prioritize_work_items, score_work_item


def test_due_urgent_item_ranks_first() -> None:
    now = datetime(2026, 7, 29, 9, 0, tzinfo=UTC)
    urgent = make_work_item(
        title="Fix final demo blocker",
        priority="urgent",
        due_at="2026-07-29T10:00:00Z",
    )
    normal = make_work_item(title="Polish docs", priority="normal")

    ranked = prioritize_work_items([normal, urgent], now=now)

    assert ranked[0].item.id == urgent.id
    assert "due within 24h" in ranked[0].reasons


def test_completed_items_are_excluded_from_priorities() -> None:
    open_item = make_work_item(title="Open work")
    done_item = make_work_item(title="Done work")
    done_item = done_item.__class__.from_mapping({**done_item.to_dict(), "status": "completed"})

    ranked = prioritize_work_items([done_item, open_item])

    assert [scored.item.title for scored in ranked] == ["Open work"]


def test_blocked_item_gets_reason_and_penalty() -> None:
    blocked = make_work_item(title="Blocked work", priority="high")
    blocked = blocked.__class__.from_mapping({**blocked.to_dict(), "status": "blocked"})

    scored = score_work_item(blocked)

    assert "blocked" in scored.reasons
    assert scored.score < score_work_item(make_work_item(title="Unblocked", priority="high")).score
