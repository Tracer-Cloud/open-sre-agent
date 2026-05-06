"""Assign and notify **new** contributors on `good first issue` threads.

Here *new* means the commenter has **no merged PRs and no open PRs** in this repo where
they are the PR author (GitHub Search API). **One or more merged PRs** means they are not
treated as a first-time contributor for this automation (GitHub's ``FIRST_TIME_CONTRIBUTOR``
/ ``FIRST_TIMER`` flags are not used).

Also skips repo insiders (OWNER / MEMBER / COLLABORATOR), bots, closed issues,
comments on **pull request** threads (``issue_comment`` fires for PRs too; those
use ``issue.pull_request``), and commenters already listed as assignees.

**One open assignment per eligible new contributor** in this repo: if they already
have another **open** issue assigned (Search API), they cannot be auto-assigned here.

At most **one** auto-assignment per issue: if anyone else is already an assignee,
further eligible commenters are skipped (manual pre-assignments count).
"""

from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

GOOD_FIRST_LABEL = "good first issue"
# Do not auto-assign maintainers/collaborators;
# eligibility is 0 merged + 0 open PRs as author + not insider.
EXCLUDED_COMMENTER_ASSOCIATIONS = frozenset({"OWNER", "MEMBER", "COLLABORATOR"})
GITHUB_API = "https://api.github.com"


def screen_event_without_api(event: dict[str, Any]) -> str | None:
    """Return a skip reason before calling the GitHub API, or None if checks should continue."""
    issue = event.get("issue") or {}
    comment = event.get("comment") or {}
    if issue.get("pull_request") is not None:
        return "comment_on_pull_request"
    if issue.get("state") != "open":
        return "issue_not_open"
    labels = issue.get("labels") or []
    if not isinstance(labels, list):
        return "invalid_labels"
    names = {item.get("name") for item in labels if isinstance(item, dict)}
    if GOOD_FIRST_LABEL not in names:
        return "not_good_first_issue"

    c_user = comment.get("user") or {}
    if c_user.get("type") == "Bot":
        return "bot_commenter"
    c_login = c_user.get("login") or ""
    if not c_login:
        return "missing_commenter_login"

    c_assoc = comment.get("author_association") or ""
    if c_assoc in EXCLUDED_COMMENTER_ASSOCIATIONS:
        return "commenter_repo_insider"

    assignees = issue.get("assignees") or []
    if isinstance(assignees, list):
        assigned_logins = {
            a.get("login") for a in assignees if isinstance(a, dict) and a.get("login")
        }
        if c_login in assigned_logins:
            return "already_assignee"
        if assigned_logins:
            return "issue_already_claimed"

    return None


def assign_decision(
    *,
    skip_reason_pre_api: str | None,
    merged_pr_count_for_commenter: int,
    open_pr_count_for_commenter: int,
    open_assigned_issue_count_for_commenter: int,
) -> tuple[bool, str]:
    """Return (should_assign_and_comment, skip_reason_or_empty).

    Eligible "new contributor" means ``merged_pr_count_for_commenter == 0`` and
    ``open_pr_count_for_commenter == 0`` (PR author in this repo, via Search API).
    They must also have no other open issues assigned in this repo
    (``open_assigned_issue_count_for_commenter == 0``).
    """
    if skip_reason_pre_api is not None:
        return False, skip_reason_pre_api
    if open_pr_count_for_commenter > 0:
        return False, "has_open_prs"
    if merged_pr_count_for_commenter > 0:
        return False, "has_merged_prs"
    if open_assigned_issue_count_for_commenter > 0:
        return False, "already_has_open_assigned_issue"
    return True, ""


def _request_json(url: str, token: str) -> Any:
    req = urllib.request.Request(
        url,
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        },
        method="GET",
    )
    with urllib.request.urlopen(req, timeout=60) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _search_issue_total_count(query: str, token: str) -> int:
    params = urllib.parse.urlencode({"q": query})
    url = f"{GITHUB_API}/search/issues?{params}"
    try:
        data = _request_json(url, token)
    except urllib.error.URLError as exc:
        print(f"GitHub search failed: {exc}", file=sys.stderr)
        raise
    total = data.get("total_count")
    if not isinstance(total, int):
        return 0
    return total


def fetch_merged_pr_count(owner: str, repo: str, login: str, token: str) -> int:
    q = f"repo:{owner}/{repo} is:pr is:merged author:{login}"
    return _search_issue_total_count(q, token)


def fetch_open_pr_count(owner: str, repo: str, login: str, token: str) -> int:
    q = f"repo:{owner}/{repo} is:pr is:open author:{login}"
    return _search_issue_total_count(q, token)


def fetch_open_assigned_issue_count(owner: str, repo: str, login: str, token: str) -> int:
    """Count open issues in this repo where ``login`` is an assignee."""
    q = f"repo:{owner}/{repo} is:issue is:open assignee:{login}"
    return _search_issue_total_count(q, token)


def build_assign_notice_body(*, assignee_login: str) -> str:
    return f"@{assignee_login} You've been **assigned** to this issue. Thanks for picking it up."


def set_github_output(name: str, value: str) -> None:
    path = os.environ.get("GITHUB_OUTPUT")
    if not path:
        return
    with open(path, "a", encoding="utf-8") as fh:
        fh.write(f"{name}={value}\n")


def main() -> int:
    event_path = os.environ.get("GITHUB_EVENT_PATH")
    repository = os.environ.get("GITHUB_REPOSITORY")
    token = os.environ.get("GITHUB_TOKEN")
    if not event_path or not repository or not token:
        print("Missing GITHUB_EVENT_PATH, GITHUB_REPOSITORY, or GITHUB_TOKEN.", file=sys.stderr)
        return 1

    raw = Path(event_path).read_text(encoding="utf-8")
    event = json.loads(raw)

    pre = screen_event_without_api(event)
    merged_count = 0
    open_count = 0
    open_assigned_issue_count = 0

    if pre is None:
        owner, _, repo = repository.partition("/")
        if not owner or not repo:
            print("Invalid GITHUB_REPOSITORY.", file=sys.stderr)
            return 1
        comment = event.get("comment") or {}
        c_login = (comment.get("user") or {}).get("login") or ""
        try:
            merged_count = fetch_merged_pr_count(owner, repo, c_login, token)
            open_count = fetch_open_pr_count(owner, repo, c_login, token)
            open_assigned_issue_count = fetch_open_assigned_issue_count(owner, repo, c_login, token)
        except urllib.error.URLError as exc:
            print(f"GitHub API request failed: {exc}", file=sys.stderr)
            return 1

    should, reason = assign_decision(
        skip_reason_pre_api=pre,
        merged_pr_count_for_commenter=merged_count,
        open_pr_count_for_commenter=open_count,
        open_assigned_issue_count_for_commenter=open_assigned_issue_count,
    )

    if not should:
        print(f"Skip: {reason}")
        set_github_output("should_assign", "false")
        return 0

    comment_user = (event.get("comment") or {}).get("user") or {}
    login = comment_user.get("login") if isinstance(comment_user, dict) else ""
    if not isinstance(login, str) or not login:
        print("Missing commenter login.", file=sys.stderr)
        return 1

    body = build_assign_notice_body(assignee_login=login)
    Path("assign_comment.md").write_text(body, encoding="utf-8")
    set_github_output("should_assign", "true")
    print("Wrote assign_comment.md; assignment will be applied in workflow.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
