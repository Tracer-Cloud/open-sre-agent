"""Deterministic prioritization for durable work items."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime

from core.domain.work_items.models import WorkItem, WorkItemPriority, WorkItemStatus

_PRIORITY_WEIGHTS: dict[WorkItemPriority, int] = {
    WorkItemPriority.URGENT: 90,
    WorkItemPriority.HIGH: 65,
    WorkItemPriority.NORMAL: 35,
    WorkItemPriority.LOW: 15,
}


@dataclass(frozen=True)
class WorkItemScore:
    item: WorkItem
    score: int
    reasons: tuple[str, ...]


def score_work_item(item: WorkItem, *, now: datetime | None = None) -> WorkItemScore:
    current = now or datetime.now(UTC)
    reasons: list[str] = [f"{item.priority.value} priority"]
    score = _PRIORITY_WEIGHTS[item.priority]

    due_at = _parse_datetime(item.due_at)
    if due_at is not None:
        delta_hours = (due_at - current).total_seconds() / 3600
        if delta_hours < 0:
            score += 55
            reasons.append("overdue")
        elif delta_hours <= 24:
            score += 40
            reasons.append("due within 24h")
        elif delta_hours <= 72:
            score += 25
            reasons.append("due within 3 days")
        elif delta_hours <= 168:
            score += 10
            reasons.append("due this week")

    if item.remind_at:
        remind_at = _parse_datetime(item.remind_at)
        if remind_at is not None and remind_at <= current:
            score += 15
            reasons.append("reminder is due")

    created_at = _parse_datetime(item.created_at)
    if created_at is not None:
        age_days = int(max((current - created_at).total_seconds(), 0) // 86400)
        if age_days >= 7:
            boost = min(age_days, 14)
            score += boost
            reasons.append(f"open for {age_days}d")

    if item.status is WorkItemStatus.BLOCKED:
        score -= 25
        reasons.append("blocked")
    elif item.status is WorkItemStatus.DEFERRED:
        score -= 15
        reasons.append("deferred")
    elif item.status is WorkItemStatus.COMPLETED:
        score = -1
        reasons = ["completed"]

    return WorkItemScore(item=item, score=score, reasons=tuple(reasons))


def prioritize_work_items(
    items: Sequence[WorkItem],
    *,
    limit: int = 5,
    now: datetime | None = None,
) -> list[WorkItemScore]:
    scores = [
        score_work_item(item, now=now)
        for item in items
        if item.status is not WorkItemStatus.COMPLETED
    ]
    scores.sort(
        key=lambda scored: (
            -scored.score,
            _priority_rank(scored.item.priority),
            scored.item.created_at,
            scored.item.title.lower(),
        )
    )
    return scores[: max(limit, 0)]


def _priority_rank(priority: WorkItemPriority) -> int:
    return {
        WorkItemPriority.URGENT: 0,
        WorkItemPriority.HIGH: 1,
        WorkItemPriority.NORMAL: 2,
        WorkItemPriority.LOW: 3,
    }[priority]


def _parse_datetime(value: str) -> datetime | None:
    text = value.strip()
    if not text:
        return None
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


__all__ = ["WorkItemScore", "prioritize_work_items", "score_work_item"]
