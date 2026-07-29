"""Message builders for work-item reminders and check-ins."""

from __future__ import annotations

from collections.abc import Sequence

from core.domain.work_items.models import WorkItem
from core.domain.work_items.scoring import prioritize_work_items


def build_work_item_reminder_message(item: WorkItem) -> str:
    lines = [
        "OpenSRE reminder",
        "",
        f"Task: {item.title}",
        f"ID: {item.display_id}",
        f"Priority: {item.priority.value}",
    ]
    if item.project:
        lines.append(f"Project: {item.project}")
    if item.owner:
        lines.append(f"Owner: {item.owner}")
    if item.due_at:
        lines.append(f"Due: {item.due_at}")
    if item.notes:
        lines.extend(["", item.notes])
    lines.extend(["", f"Mark done with: /work done {item.display_id}"])
    return "\n".join(lines)


def build_work_item_checkin_message(
    items: Sequence[WorkItem],
    *,
    project: str = "",
    limit: int = 5,
) -> str:
    ranked = prioritize_work_items(items, limit=limit)
    if not ranked:
        return ""
    title = "OpenSRE work check-in"
    if project.strip():
        title += f" for {project.strip()}"
    lines = [title, "", "Highest-leverage open work:"]
    for index, scored in enumerate(ranked, start=1):
        item = scored.item
        suffix = f" [{item.priority.value}]"
        if item.due_at:
            suffix += f" due {item.due_at}"
        lines.append(f"{index}. {item.title}{suffix} ({item.display_id})")
        if scored.reasons:
            lines.append(f"   Why: {', '.join(scored.reasons)}")
    lines.extend(["", "Ask “what should I focus on next?” for a ranked pick."])
    return "\n".join(lines)


__all__ = ["build_work_item_checkin_message", "build_work_item_reminder_message"]
