"""Report composition helpers for GitHub workflow tools."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from tools.github.workflow.models import PullRequestStatus, WorkItem, WorkStatusReport


def _as_dict(item: WorkItem | PullRequestStatus | dict[str, Any]) -> dict[str, Any]:
    if isinstance(item, WorkItem | PullRequestStatus):
        return item.to_dict()
    return item


def _item_line(item: dict[str, Any]) -> str:
    assignees = item.get("assignees") or []
    owner = f" — @{', @'.join(assignees)}" if assignees else ""
    return f"• #{item.get('number', '?')} {item.get('title', '')}{owner}"


def _pr_line(pr: dict[str, Any]) -> str:
    reasons = pr.get("blocking_reasons") or []
    reason_text = f" — {', '.join(str(reason) for reason in reasons)}" if reasons else ""
    return f"• PR #{pr.get('number', '?')} {pr.get('title', '')}{reason_text}"


def _recommended_actions(
    *,
    up_for_grabs: list[dict[str, Any]],
    unassigned: list[dict[str, Any]],
    blocked_prs: list[dict[str, Any]],
    mergeable_prs: list[dict[str, Any]],
    errors: list[str],
) -> list[str]:
    actions: list[str] = []
    if errors:
        actions.append("• Re-run failed GitHub reads before trusting this status report.")
    if blocked_prs:
        actions.append(f"• Unblock {len(blocked_prs)} PR(s) before starting new work.")
    if mergeable_prs:
        actions.append(f"• Review or merge {len(mergeable_prs)} ready PR(s).")
    if up_for_grabs:
        actions.append(f"• Assign {len(up_for_grabs)} up-for-grabs task(s).")
    if unassigned:
        actions.append(f"• Triage {len(unassigned)} unassigned issue(s).")
    if not actions:
        actions.append("• No obvious blockers from the supplied data.")
    return actions


def build_work_status_report(
    *,
    work_items: Sequence[WorkItem | dict[str, Any]],
    pull_requests: Sequence[PullRequestStatus | dict[str, Any]],
    context: str = "today",
    errors: list[str] | None = None,
) -> WorkStatusReport:
    """Build a Slack-ready report from an already-read GitHub snapshot."""

    errors = list(errors or [])
    item_dicts = [_as_dict(item) for item in work_items]
    pr_dicts = [_as_dict(pr) for pr in pull_requests]
    up_for_grabs = [item for item in item_dicts if item.get("work_status") == "up_for_grabs"]
    unassigned = [item for item in item_dicts if item.get("work_status") == "unassigned"]
    taken = [item for item in item_dicts if item.get("work_status") == "taken"]
    blocked_prs = [pr for pr in pr_dicts if pr.get("status") == "blocked"]
    mergeable_prs = [pr for pr in pr_dicts if pr.get("status") == "mergeable"]
    unknown_prs = [pr for pr in pr_dicts if pr.get("status") == "unknown"]

    sections = [f"*Engineering status — {context}*", ""]
    if errors:
        sections.extend(["*Incomplete report:*", *[f"• {error}" for error in errors], ""])
    sections.append(
        f"*Open work:* {len(item_dicts)} total ({len(taken)} taken, "
        f"{len(up_for_grabs)} up for grabs, {len(unassigned)} unassigned)"
    )
    if up_for_grabs:
        sections.extend(["", "*Up for grabs:*", *[_item_line(item) for item in up_for_grabs[:10]]])
    if unassigned:
        sections.extend(["", "*Unassigned:*", *[_item_line(item) for item in unassigned[:10]]])
    if blocked_prs:
        sections.extend(["", "*Blocked PRs:*", *[_pr_line(pr) for pr in blocked_prs[:10]]])
    if unknown_prs:
        sections.extend(["", "*Unknown PR status:*", *[_pr_line(pr) for pr in unknown_prs[:10]]])
    if mergeable_prs:
        sections.extend(["", "*Ready to merge:*", *[_pr_line(pr) for pr in mergeable_prs[:10]]])
    sections.extend(
        [
            "",
            "*Recommended next actions:*",
            *_recommended_actions(
                up_for_grabs=up_for_grabs,
                unassigned=unassigned,
                blocked_prs=blocked_prs,
                mergeable_prs=mergeable_prs,
                errors=errors,
            ),
        ]
    )

    return WorkStatusReport(
        counts={
            "open_work": len(item_dicts),
            "taken": len(taken),
            "up_for_grabs": len(up_for_grabs),
            "unassigned": len(unassigned),
            "blocked_prs": len(blocked_prs),
            "mergeable_prs": len(mergeable_prs),
            "unknown_prs": len(unknown_prs),
        },
        slack_markdown="\n".join(sections).strip(),
        available=not errors,
        incomplete=bool(errors),
        errors=errors,
    )
