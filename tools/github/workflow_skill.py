"""Pure workflow policy for GitHub-backed engineering coordination.

This module is intentionally not a registered tool. It is the reusable "skill"
layer that composes the small GitHub primitives into human workflows while
keeping network I/O and side effects in thin tool wrappers.
"""

from __future__ import annotations

from typing import Any, Literal

SlackTaskOperation = Literal["create", "update", "close"]


def _item_line(item: dict[str, Any]) -> str:
    assignees = item.get("assignees") or []
    owner = f" — @{', @'.join(assignees)}" if assignees else ""
    return f"• #{item.get('number', '?')} {item.get('title', '')}{owner}"


def _pr_line(pr: dict[str, Any]) -> str:
    reasons = pr.get("blocking_reasons") or []
    reason_text = f" — {', '.join(str(reason) for reason in reasons)}" if reasons else ""
    return f"• PR #{pr.get('number', '?')} {pr.get('title', '')}{reason_text}"


def recommended_work_actions(
    *,
    up_for_grabs: list[dict[str, Any]],
    unassigned: list[dict[str, Any]],
    blocked_prs: list[dict[str, Any]],
    mergeable_prs: list[dict[str, Any]],
) -> list[str]:
    """Prioritized next actions for a work-status report."""

    actions: list[str] = []
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
    work_items: list[dict[str, Any]],
    pull_requests: list[dict[str, Any]],
    context: str = "today",
) -> dict[str, Any]:
    """Build a Slack-ready work-status report from already-fetched GitHub data."""

    up_for_grabs = [item for item in work_items if item.get("work_status") == "up_for_grabs"]
    unassigned = [item for item in work_items if item.get("work_status") == "unassigned"]
    taken = [item for item in work_items if item.get("work_status") == "taken"]
    blocked_prs = [pr for pr in pull_requests if pr.get("status") == "blocked"]
    mergeable_prs = [pr for pr in pull_requests if pr.get("status") == "mergeable"]

    sections = [f"*Engineering status — {context}*", ""]
    sections.append(
        f"*Open work:* {len(work_items)} total ({len(taken)} taken, "
        f"{len(up_for_grabs)} up for grabs, {len(unassigned)} unassigned)"
    )
    if up_for_grabs:
        sections.extend(["", "*Up for grabs:*", *[_item_line(item) for item in up_for_grabs[:10]]])
    if unassigned:
        sections.extend(["", "*Unassigned:*", *[_item_line(item) for item in unassigned[:10]]])
    if blocked_prs:
        sections.extend(["", "*Blocked PRs:*", *[_pr_line(pr) for pr in blocked_prs[:10]]])
    if mergeable_prs:
        sections.extend(["", "*Ready to merge:*", *[_pr_line(pr) for pr in mergeable_prs[:10]]])
    sections.extend(
        [
            "",
            "*Recommended next actions:*",
            *recommended_work_actions(
                up_for_grabs=up_for_grabs,
                unassigned=unassigned,
                blocked_prs=blocked_prs,
                mergeable_prs=mergeable_prs,
            ),
        ]
    )

    return {
        "counts": {
            "open_work": len(work_items),
            "taken": len(taken),
            "up_for_grabs": len(up_for_grabs),
            "unassigned": len(unassigned),
            "blocked_prs": len(blocked_prs),
            "mergeable_prs": len(mergeable_prs),
        },
        "slack_markdown": "\n".join(sections).strip(),
        "side_effects": [],
    }


def normalize_community_comment(
    raw: dict[str, Any],
    issue: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Normalize GitHub issue comment payloads and test-provided comments."""

    issue = issue or {}
    return {
        "issue_number": raw.get("issue_number", issue.get("number")),
        "issue_title": raw.get("issue_title", issue.get("title", "")),
        "author": raw.get("author") or (raw.get("user") or {}).get("login", ""),
        "body": str(raw.get("body", "")),
        "created_at": str(raw.get("created_at", "")),
        "url": raw.get("url") or raw.get("html_url", ""),
    }


def _is_question(text: str) -> bool:
    lowered = text.lower()
    return "?" in text or lowered.startswith(
        ("when ", "what ", "who ", "how ", "where ", "can ", "could ")
    )


def _is_agenda_item(text: str) -> bool:
    lowered = text.lower()
    return any(term in lowered for term in ("agenda", "standup"))


def _question_answered_later(
    question: dict[str, Any],
    comments: list[dict[str, Any]],
    maintainers: set[str],
) -> bool:
    q_issue = question.get("issue_number")
    q_created = str(question.get("created_at", ""))
    for comment in comments:
        if comment.get("issue_number") != q_issue:
            continue
        if str(comment.get("created_at", "")) <= q_created:
            continue
        if str(comment.get("author", "")).lower() in maintainers:
            return True
    return False


def _suggest_reply(question: dict[str, Any]) -> dict[str, Any]:
    return {
        "issue_number": question.get("issue_number"),
        "issue_title": question.get("issue_title", ""),
        "context": question.get("body", ""),
        "suggested_reply": (
            "Thanks for the question — we should confirm the current owner/status "
            "and reply in this thread with the next concrete step."
        ),
        "url": question.get("url", ""),
    }


def summarize_community_followups_from_comments(
    *,
    comments: list[dict[str, Any]],
    maintainer_logins: list[str] | None = None,
) -> dict[str, Any]:
    """Summarize unanswered questions and agenda items from normalized comments."""

    normalized_comments = [normalize_community_comment(comment) for comment in comments]
    maintainers = {login.lower() for login in (maintainer_logins or [])}
    questions = [
        comment for comment in normalized_comments if _is_question(str(comment.get("body", "")))
    ]
    unanswered = [
        question
        for question in questions
        if str(question.get("author", "")).lower() not in maintainers
        and not _question_answered_later(question, normalized_comments, maintainers)
    ]
    agenda_items = [
        comment for comment in normalized_comments if _is_agenda_item(str(comment.get("body", "")))
    ]
    return {
        "unanswered_questions": unanswered,
        "agenda_items": agenda_items,
        "suggested_replies": [_suggest_reply(question) for question in unanswered],
        "counts": {
            "comments": len(normalized_comments),
            "unanswered_questions": len(unanswered),
            "agenda_items": len(agenda_items),
        },
        "side_effects": [],
    }


def title_from_slack_text(slack_text: str) -> str:
    """Derive a compact issue title from a Slack request."""

    cleaned = " ".join(slack_text.strip().split())
    if not cleaned:
        return "Task from Slack"
    return cleaned[:80].rstrip(" .")


def issue_body_from_slack(slack_text: str, slack_url: str) -> str:
    """Build a GitHub issue body that preserves the Slack source link."""

    body = ["## Slack request", "", slack_text.strip() or "(No Slack text provided.)"]
    if slack_url.strip():
        body.extend(["", f"Source: {slack_url.strip()}"])
    return "\n".join(body)


def build_slack_task_payload(
    *,
    operation: SlackTaskOperation,
    slack_text: str,
    slack_url: str = "",
    issue_number: int | None = None,
    title: str = "",
    labels: list[str] | None = None,
    assignees: list[str] | None = None,
) -> dict[str, Any]:
    """Build the GitHub issue payload for a Slack-sourced task operation."""

    if operation == "create":
        payload: dict[str, Any] = {
            "title": title.strip() or title_from_slack_text(slack_text),
            "body": issue_body_from_slack(slack_text, slack_url),
        }
    elif operation == "update":
        payload = {"number": issue_number, "body": issue_body_from_slack(slack_text, slack_url)}
        if title.strip():
            payload["title"] = title.strip()
    else:
        payload = {
            "number": issue_number,
            "state": "closed",
            "state_reason": "completed",
            "body": issue_body_from_slack(slack_text, slack_url),
        }

    if labels is not None and operation in {"create", "update"}:
        payload["labels"] = labels
    if assignees is not None and operation in {"create", "update"}:
        payload["assignees"] = assignees
    return payload


def dry_run_slack_task_result(side_effect: str, payload: dict[str, Any]) -> dict[str, Any]:
    """Return a standard non-mutating response for confirmation-gated task tools."""

    return {
        "source": "github",
        "available": True,
        "executed": False,
        "side_effect": side_effect,
        "issue": payload,
    }


def slack_task_success(side_effect: str, issue: Any) -> dict[str, Any]:
    """Return a standard success response for Slack task mutations."""

    return {
        "source": "github",
        "available": True,
        "executed": True,
        "side_effect": side_effect,
        "issue": issue,
    }


def slack_task_failure(side_effect: str, error: Exception) -> dict[str, Any]:
    """Return a standard failure response for Slack task mutations."""

    return {
        "source": "github",
        "available": False,
        "executed": False,
        "error": str(error),
        "side_effect": side_effect,
    }
