"""Community follow-up summarization for GitHub workflow tools."""

from __future__ import annotations

import re
from typing import Any

from tools.github.workflow.models import CommunityFollowup

_ISSUE_NUMBER_RE = re.compile(r"/issues/(?P<number>\d+)(?:$|[#?])")


def issue_number_from_url(url: str) -> int | None:
    match = _ISSUE_NUMBER_RE.search(url)
    if match is None:
        return None
    return int(match.group("number"))


def normalize_community_comment(raw: dict[str, Any]) -> CommunityFollowup:
    """Normalize GitHub repository issue-comment payloads."""

    issue_number = raw.get("issue_number")
    if not isinstance(issue_number, int):
        issue_url = str(raw.get("issue_url") or raw.get("html_url") or "")
        issue_number = issue_number_from_url(issue_url)
    return CommunityFollowup(
        issue_number=issue_number,
        issue_title=str(raw.get("issue_title", "")),
        author=str(raw.get("author") or (raw.get("user") or {}).get("login", "")),
        body=str(raw.get("body", "")),
        created_at=str(raw.get("created_at", "")),
        url=str(raw.get("url") or raw.get("html_url") or ""),
    )


def _is_question(text: str) -> bool:
    lowered = text.lower()
    return "?" in text or lowered.startswith(
        ("when ", "what ", "who ", "how ", "where ", "can ", "could ")
    )


def _is_agenda_item(text: str) -> bool:
    lowered = text.lower()
    return any(term in lowered for term in ("agenda", "standup"))


def _question_answered_later(
    question: CommunityFollowup,
    comments: list[CommunityFollowup],
    maintainers: set[str],
) -> bool:
    for comment in comments:
        if comment.issue_number != question.issue_number:
            continue
        if comment.created_at <= question.created_at:
            continue
        if comment.author.lower() in maintainers:
            return True
    return False


def _suggest_reply(question: CommunityFollowup) -> dict[str, Any]:
    return {
        "issue_number": question.issue_number,
        "issue_title": question.issue_title,
        "context": question.body,
        "suggested_reply": (
            "Thanks for the question — we should confirm the current owner/status "
            "and reply in this thread with the next concrete step."
        ),
        "url": question.url,
    }


def summarize_community_followups_from_comments(
    *,
    comments: list[dict[str, Any]],
    maintainer_logins: list[str] | None = None,
) -> dict[str, Any]:
    normalized_comments = [normalize_community_comment(comment) for comment in comments]
    maintainers = {login.lower() for login in (maintainer_logins or [])}
    questions = [comment for comment in normalized_comments if _is_question(comment.body)]
    unanswered = [
        question
        for question in questions
        if question.author.lower() not in maintainers
        and not _question_answered_later(question, normalized_comments, maintainers)
    ]
    agenda_items = [comment for comment in normalized_comments if _is_agenda_item(comment.body)]
    return {
        "unanswered_questions": [question.to_dict() for question in unanswered],
        "agenda_items": [item.to_dict() for item in agenda_items],
        "suggested_replies": [_suggest_reply(question) for question in unanswered],
        "counts": {
            "comments": len(normalized_comments),
            "unanswered_questions": len(unanswered),
            "agenda_items": len(agenda_items),
        },
        "side_effects": [],
    }
