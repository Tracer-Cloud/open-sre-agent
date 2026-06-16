#!/usr/bin/env python3
"""Merge a pull request when it is labeled automerge and CI checks are green."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from typing import Any

AUTOMERGE_LABEL = "automerge"
ALLOWED_CONCLUSIONS = frozenset({"SUCCESS", "SKIPPED", "NEUTRAL"})


def _run_gh(args: list[str]) -> Any:
    result = subprocess.run(
        ["gh", *args],
        check=True,
        capture_output=True,
        text=True,
    )
    return json.loads(result.stdout)


def _checks_are_green(status_rollup: list[dict[str, Any]]) -> tuple[bool, str]:
    if not status_rollup:
        return False, "no status checks reported yet"

    for check in status_rollup:
        name = check.get("name", "unknown")
        status = check.get("status", "")
        conclusion = check.get("conclusion") or ""

        if status in {"IN_PROGRESS", "QUEUED", "PENDING", "WAITING", "REQUESTED"}:
            return False, f"check still running: {name}"

        if status != "COMPLETED":
            return False, f"unexpected check status for {name}: {status}"

        if conclusion not in ALLOWED_CONCLUSIONS:
            return False, f"check not green: {name} ({conclusion or 'missing conclusion'})"

    return True, "all checks green"


def main() -> int:
    repo = os.environ["GITHUB_REPOSITORY"]
    pr_number = os.environ["PR_NUMBER"]

    pr = _run_gh(
        [
            "pr",
            "view",
            pr_number,
            "--repo",
            repo,
            "--json",
            "baseRefName,isDraft,mergeable,mergeStateStatus,labels,state,statusCheckRollup,title",
        ]
    )

    if pr.get("baseRefName") != "main":
        print(f"PR #{pr_number} does not target main; skipping.")
        return 0

    if pr.get("state") != "OPEN":
        print(f"PR #{pr_number} is not open; skipping.")
        return 0

    if pr.get("isDraft"):
        print(f"PR #{pr_number} is a draft; skipping.")
        return 0

    label_names = {label["name"] for label in pr.get("labels", [])}
    if AUTOMERGE_LABEL not in label_names:
        print(f"PR #{pr_number} does not have the {AUTOMERGE_LABEL} label; skipping.")
        return 0

    if pr.get("mergeable") != "MERGEABLE":
        print(f"PR #{pr_number} is not mergeable ({pr.get('mergeStateStatus')}); skipping.")
        return 0

    green, reason = _checks_are_green(pr.get("statusCheckRollup") or [])
    if not green:
        print(f"PR #{pr_number} not ready to merge: {reason}")
        return 0

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
            title,
        ],
        check=True,
    )
    print(f"Merged PR #{pr_number}.")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except subprocess.CalledProcessError as exc:
        print(exc.stderr or exc.stdout or str(exc), file=sys.stderr)
        raise SystemExit(exc.returncode) from exc
