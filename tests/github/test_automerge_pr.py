from __future__ import annotations

import importlib.util
from pathlib import Path

_MODULE_PATH = Path(__file__).resolve().parents[2] / ".github" / "scripts" / "automerge_pr.py"
_spec = importlib.util.spec_from_file_location("automerge_pr", _MODULE_PATH)
assert _spec and _spec.loader
automerge_pr = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(automerge_pr)


def test_squash_commit_subject_appends_pr_number() -> None:
    assert (
        automerge_pr._squash_commit_subject("fix(cli): show full root cause", "3025")
        == "fix(cli): show full root cause (#3025)"
    )


def test_squash_commit_subject_avoids_duplicate_pr_number() -> None:
    assert (
        automerge_pr._squash_commit_subject("fix(cli): example (#3025)", "3025")
        == "fix(cli): example (#3025)"
    )


def test_checks_are_green_for_completed_check_runs() -> None:
    green, reason = automerge_pr._checks_are_green(
        [
            {
                "__typename": "CheckRun",
                "name": "quality (ubuntu-latest)",
                "status": "COMPLETED",
                "conclusion": "SUCCESS",
            },
            {
                "__typename": "CheckRun",
                "name": "windows test",
                "status": "COMPLETED",
                "conclusion": "SKIPPED",
            },
        ]
    )
    assert green is True
    assert reason == "all checks green"


def test_checks_are_green_for_successful_status_contexts() -> None:
    green, reason = automerge_pr._checks_are_green(
        [
            {
                "__typename": "StatusContext",
                "context": "ci/legacy",
                "state": "SUCCESS",
            }
        ]
    )
    assert green is True
    assert reason == "all checks green"


def test_checks_are_green_mixed_check_run_and_status_context() -> None:
    green, reason = automerge_pr._checks_are_green(
        [
            {
                "__typename": "CheckRun",
                "name": "quality (ubuntu-latest)",
                "status": "COMPLETED",
                "conclusion": "SUCCESS",
            },
            {
                "__typename": "StatusContext",
                "context": "ci/legacy",
                "state": "SUCCESS",
            },
        ]
    )
    assert green is True
    assert reason == "all checks green"


def test_status_context_failure_blocks_merge() -> None:
    green, reason = automerge_pr._checks_are_green(
        [
            {
                "__typename": "StatusContext",
                "context": "ci/legacy",
                "state": "FAILURE",
            }
        ]
    )
    assert green is False
    assert reason == "status not green: ci/legacy (FAILURE)"


def test_status_context_pending_blocks_merge() -> None:
    green, reason = automerge_pr._checks_are_green(
        [
            {
                "__typename": "StatusContext",
                "context": "ci/legacy",
                "state": "PENDING",
            }
        ]
    )
    assert green is False
    assert reason == "status still pending: ci/legacy"


def test_ignores_automerge_workflow_check_while_running() -> None:
    green, reason = automerge_pr._checks_are_green(
        [
            {
                "__typename": "CheckRun",
                "name": "quality (ubuntu-latest)",
                "status": "COMPLETED",
                "conclusion": "SUCCESS",
            },
            {
                "__typename": "CheckRun",
                "name": "Merge when CI is green",
                "workflowName": "Auto-merge",
                "status": "IN_PROGRESS",
                "conclusion": None,
            },
        ]
    )
    assert green is True
    assert reason == "all checks green"
