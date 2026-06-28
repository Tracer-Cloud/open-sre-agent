"""Mutation proposal helpers for GitHub issue workflow tools."""

from __future__ import annotations

import hashlib
import json
from typing import Any

from tools.github.workflow.models import GitHubIssueMutationProposal, IssueMutationOperation


def _proposal_digest(data: dict[str, Any]) -> str:
    raw = json.dumps(data, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


def _issue_body(slack_text: str, slack_url: str, marker: str) -> str:
    body = ["## Slack request", "", slack_text.strip() or "(No Slack text provided.)"]
    if slack_url.strip():
        body.extend(["", f"Source: {slack_url.strip()}"])
    body.extend(["", f"<!-- {marker} -->"])
    return "\n".join(body)


def _comment_body(slack_text: str, slack_url: str, marker: str) -> str:
    body = ["Slack follow-up:", "", slack_text.strip() or "(No Slack text provided.)"]
    if slack_url.strip():
        body.extend(["", f"Source: {slack_url.strip()}"])
    body.extend(["", f"<!-- {marker} -->"])
    return "\n".join(body)


def title_from_slack_text(slack_text: str) -> str:
    cleaned = " ".join(slack_text.strip().split())
    if not cleaned:
        return "Task from Slack"
    return cleaned[:80].rstrip(" .")


def build_issue_mutation_proposal(
    *,
    owner: str,
    repo: str,
    operation: IssueMutationOperation,
    slack_text: str,
    slack_url: str = "",
    issue_number: int | None = None,
    title: str = "",
    labels: list[str] | None = None,
    assignees: list[str] | None = None,
) -> GitHubIssueMutationProposal:
    seed = {
        "owner": owner,
        "repo": repo,
        "operation": operation,
        "issue_number": issue_number,
        "slack_text": " ".join(slack_text.split()),
        "slack_url": slack_url.strip(),
        "title": title.strip(),
        "labels": labels or [],
        "assignees": assignees or [],
    }
    proposal_id = f"ghslack-{_proposal_digest(seed)}"
    marker = f"opensre-slack-proposal:{proposal_id}"
    target = {"issue_number": issue_number} if issue_number is not None else {}

    if operation == "create":
        payload: dict[str, Any] = {
            "title": title.strip() or title_from_slack_text(slack_text),
            "body": _issue_body(slack_text, slack_url, marker),
        }
        if labels is not None:
            payload["labels"] = labels
        if assignees is not None:
            payload["assignees"] = assignees
    elif operation == "update":
        payload = {"comment_body": _comment_body(slack_text, slack_url, marker)}
        if title.strip():
            payload["title"] = title.strip()
        if labels is not None:
            payload["labels"] = labels
        if assignees is not None:
            payload["assignees"] = assignees
    else:
        payload = {
            "comment_body": _comment_body(slack_text, slack_url, marker),
            "state": "closed",
            "state_reason": "completed",
        }

    return GitHubIssueMutationProposal(
        proposal_id=proposal_id,
        operation=operation,
        owner=owner,
        repo=repo,
        target=target,
        payload=payload,
        slack_url=slack_url.strip(),
        idempotency_marker=marker,
    )
