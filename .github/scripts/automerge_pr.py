#!/usr/bin/env python3
"""Merge a pull request when it is labeled automerge and CI checks are green."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from typing import Any

AUTOMERGE_LABEL = "automerge"
#: Paths a machine must never merge on its own. The first three carry the
#: agent runtime, the multi-tenant platform and the chat gateway — a bad
#: change there reaches every user. The last two are the merge machinery
#: itself, which must not be able to widen its own permissions.
#: Page size ``gh pr view --json files`` returns; a full page may be cut short.
FILE_PAGE_SIZE = 100
PROTECTED_PATH_PREFIXES = (
    "core/",
    "platform/",
    "gateway/",
    ".github/workflows/",
    ".github/scripts/",
)
#: ``mergeStateStatus`` when the only thing blocking a PR is that it has not
#: taken the latest base. Set by "require branches to be up to date".
MERGE_STATE_BEHIND = "BEHIND"
#: What a single pass did, so the sweep can stop after one mutation.
ACTION_NONE = "none"
ACTION_UPDATED = "updated"
ACTION_MERGED = "merged"
AUTOMERGE_WORKFLOW_NAME = "Auto-merge"
AUTOMERGE_JOB_CHECK_NAME = "Merge when CI is green"
# External app checks that are not Actions workflow_run triggers. Waiting on them
# strands labeled PRs after the last CI retry (seen with Greptile on #4196).
# Greptile remains a human gate: add `automerge` only after review is done.
AUTOMERGE_SKIPPED_CHECK_SUBSTRINGS = ("vale-spellcheck", "greptile")
CHECK_RUN_PENDING_STATUSES = frozenset({"IN_PROGRESS", "QUEUED", "PENDING", "WAITING", "REQUESTED"})
CHECK_RUN_ALLOWED_CONCLUSIONS = frozenset({"SUCCESS", "SKIPPED", "NEUTRAL"})
STATUS_CONTEXT_PENDING_STATES = frozenset({"PENDING", "EXPECTED"})
STATUS_CONTEXT_ALLOWED_STATES = frozenset({"SUCCESS"})


def _check_display_name(check: dict[str, Any]) -> str:
    return str(check.get("name") or check.get("context") or "unknown")


def _is_automerge_workflow_check(check: dict[str, Any]) -> bool:
    if check.get("workflowName") == AUTOMERGE_WORKFLOW_NAME:
        return True
    return check.get("name") == AUTOMERGE_JOB_CHECK_NAME


def _is_skipped_automerge_check(check: dict[str, Any]) -> bool:
    if _is_automerge_workflow_check(check):
        return True
    name = _check_display_name(check).casefold()
    return any(marker in name for marker in AUTOMERGE_SKIPPED_CHECK_SUBSTRINGS)


def _check_run_is_green(check: dict[str, Any]) -> tuple[bool, str]:
    name = _check_display_name(check)
    status = check.get("status", "")
    conclusion = check.get("conclusion") or ""

    if status in CHECK_RUN_PENDING_STATUSES:
        return False, f"check still running: {name}"

    if status != "COMPLETED":
        return False, f"unexpected check status for {name}: {status or 'missing status'}"

    if conclusion not in CHECK_RUN_ALLOWED_CONCLUSIONS:
        return False, f"check not green: {name} ({conclusion or 'missing conclusion'})"

    return True, ""


def _status_context_is_green(check: dict[str, Any]) -> tuple[bool, str]:
    name = _check_display_name(check)
    state = check.get("state") or ""

    if state in STATUS_CONTEXT_PENDING_STATES:
        return False, f"status still pending: {name}"

    if state not in STATUS_CONTEXT_ALLOWED_STATES:
        return False, f"status not green: {name} ({state or 'missing state'})"

    return True, ""


def _rollup_item_is_green(check: dict[str, Any]) -> tuple[bool, str]:
    typename = check.get("__typename", "")
    if typename == "StatusContext":
        return _status_context_is_green(check)
    if typename == "CheckRun":
        return _check_run_is_green(check)
    if "state" in check and "status" not in check:
        return _status_context_is_green(check)
    return _check_run_is_green(check)


def _squash_commit_subject(title: str, pr_number: str) -> str:
    suffix = f"(#{pr_number})"
    stripped = title.rstrip()
    if stripped.endswith(suffix):
        return stripped
    return f"{stripped} {suffix}"


def _checks_are_green(status_rollup: list[dict[str, Any]]) -> tuple[bool, str]:
    if not status_rollup:
        return False, "no status checks reported yet"

    for check in status_rollup:
        if _is_skipped_automerge_check(check):
            continue
        green, reason = _rollup_item_is_green(check)
        if not green:
            return False, reason

    return True, "all checks green"


def _run_gh(args: list[str]) -> Any:
    result = subprocess.run(
        ["gh", *args],
        check=True,
        capture_output=True,
        text=True,
    )
    return json.loads(result.stdout)


def _file_list_is_complete(pr: dict[str, Any]) -> bool:
    """True when every changed path is visible to the protected-path check.

    ``gh pr view --json files`` pages at 100 entries and says nothing about it,
    so a larger PR could hide a ``core/`` change past the cut and be merged as
    if it were docs. ``changedFiles`` carries the real total, so a mismatch
    means the check cannot see the whole diff and must refuse.
    """
    files = pr.get("files") or []
    changed = pr.get("changedFiles")
    if isinstance(changed, int):
        return changed <= len(files)
    # No count to compare against. A short list cannot have been truncated, but
    # a full page is exactly what truncation looks like, so refuse that.
    return len(files) < FILE_PAGE_SIZE


def _protected_paths(files: list[dict[str, Any]]) -> list[str]:
    """Paths in the PR that require a human to press merge, sorted and unique."""
    hits = {
        path
        for entry in files
        if (path := str(entry.get("path", ""))) and path.startswith(PROTECTED_PATH_PREFIXES)
    }
    return sorted(hits)


def _needs_branch_update(pr: dict[str, Any]) -> bool:
    """True when the PR is blocked only by being behind its base branch.

    A conflicted PR reports ``CONFLICTING`` and needs a human; a behind-but-clean
    PR just needs the latest base merged in so the required checks re-run against
    what would actually land.
    """
    return pr.get("mergeStateStatus") == MERGE_STATE_BEHIND and pr.get("mergeable") == "MERGEABLE"


def _update_branch(repo: str, pr_number: str) -> None:
    """Merge the base branch into the PR branch and let its checks re-run."""
    subprocess.run(
        ["gh", "pr", "update-branch", pr_number, "--repo", repo],
        check=True,
        capture_output=True,
        text=True,
    )


def _labeled_open_pr_numbers(repo: str) -> list[str]:
    """Open PRs targeting main that opted into auto-merge, oldest first.

    Oldest first makes the sweep a queue: the PR that has waited longest is
    the one refreshed, so PRs drain in order instead of all competing.
    """
    rows = _run_gh(
        [
            "pr",
            "list",
            "--repo",
            repo,
            "--state",
            "open",
            "--base",
            "main",
            "--label",
            AUTOMERGE_LABEL,
            "--json",
            "number,createdAt",
            "--limit",
            "100",
        ]
    )
    rows.sort(key=lambda row: str(row.get("createdAt") or ""))
    return [str(row["number"]) for row in rows]


def _process_pr(repo: str, pr_number: str) -> str:
    pr = _run_gh(
        [
            "pr",
            "view",
            pr_number,
            "--repo",
            repo,
            "--json",
            "baseRefName,changedFiles,files,isDraft,mergeable,mergeStateStatus,labels,state,statusCheckRollup,title",
        ]
    )

    if pr.get("baseRefName") != "main":
        print(f"PR #{pr_number} does not target main; skipping.")
        return ACTION_NONE

    if pr.get("state") != "OPEN":
        print(f"PR #{pr_number} is not open; skipping.")
        return ACTION_NONE

    if pr.get("isDraft"):
        print(f"PR #{pr_number} is a draft; skipping.")
        return ACTION_NONE

    label_names = {label["name"] for label in pr.get("labels", [])}
    if AUTOMERGE_LABEL not in label_names:
        print(f"PR #{pr_number} does not have the {AUTOMERGE_LABEL} label; skipping.")
        return ACTION_NONE

    if not _file_list_is_complete(pr):
        visible = len(pr.get("files") or [])
        print(
            f"PR #{pr_number} changes {pr.get('changedFiles')} files but only "
            f"{visible} are visible; a human must merge."
        )
        return ACTION_NONE

    protected = _protected_paths(pr.get("files") or [])
    if protected:
        shown = ", ".join(protected[:3])
        more = f" (+{len(protected) - 3} more)" if len(protected) > 3 else ""
        print(f"PR #{pr_number} touches protected paths; a human must merge: {shown}{more}")
        return ACTION_NONE

    green, reason = _checks_are_green(pr.get("statusCheckRollup") or [])
    if not green:
        print(f"PR #{pr_number} not ready to merge: {reason}")
        return ACTION_NONE

    # Only a green PR earns a refresh. Updating a red one would re-run the whole
    # suite on every merge to main and never converge.
    if _needs_branch_update(pr):
        print(f"PR #{pr_number} is behind {pr.get('baseRefName')}; updating the branch.")
        _update_branch(repo, pr_number)
        return ACTION_UPDATED

    if pr.get("mergeable") != "MERGEABLE":
        print(f"PR #{pr_number} is not mergeable ({pr.get('mergeStateStatus')}); skipping.")
        return ACTION_NONE

    title = pr["title"]
    print(f"Merging PR #{pr_number}: {title}")
    subprocess.run(
        [
            "gh",
            "pr",
            "merge",
            pr_number,
            "--repo",
            repo,
            "--squash",
            "--delete-branch",
            "--subject",
            _squash_commit_subject(title, pr_number),
        ],
        check=True,
    )
    print(f"Merged PR #{pr_number}.")
    return ACTION_MERGED


def _sweep(repo: str) -> str:
    """Advance the queue by one PR and stop.

    Exactly one mutation per sweep. Merging pushes ``main``, which triggers the
    next sweep, so the queue drains itself one PR at a time instead of
    refreshing every PR against every merge.

    A PR that cannot be refreshed — a fork that disallows maintainer edits
    answers ``gh pr update-branch`` with 403 — must not strand the ones behind
    it, so each PR is attempted independently.
    """
    for number in _labeled_open_pr_numbers(repo):
        try:
            action = _process_pr(repo, number)
        except subprocess.CalledProcessError as exc:
            detail = (exc.stderr or exc.stdout or str(exc)).strip()
            print(f"PR #{number} could not be processed; continuing. {detail}")
            continue
        if action != ACTION_NONE:
            print(f"Sweep {action} PR #{number}; stopping this pass.")
            return action
    return ACTION_NONE


def main() -> int:
    """Process one PR, or advance the queue when no number is given.

    The workflow passes ``PR_NUMBER`` for PR and check events. When ``main``
    itself moves, every open auto-merge PR falls behind at once and the sweep
    runs with no number.
    """
    repo = os.environ["GITHUB_REPOSITORY"]
    pr_number = os.environ.get("PR_NUMBER", "").strip()
    if pr_number:
        _process_pr(repo, pr_number)
    else:
        _sweep(repo)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except subprocess.CalledProcessError as exc:
        print(exc.stderr or exc.stdout or str(exc), file=sys.stderr)
        raise SystemExit(exc.returncode) from exc
