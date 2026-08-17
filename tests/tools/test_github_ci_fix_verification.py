"""Tests for post-push GitHub PR check verification."""

from __future__ import annotations

from dataclasses import replace
from unittest.mock import patch

from integrations.github.tools.ci_fix.context import CiFixContext, FailingCheck
from integrations.github.tools.ci_fix.verification import (
    DEFAULT_POLL_INTERVAL_SECONDS,
    CheckState,
    wait_for_pr_checks,
)

_CONTEXT = CiFixContext(
    owner="Tracer-Cloud",
    repo="opensre",
    number=4597,
    title="feat: add fixer",
    url="https://github.com/Tracer-Cloud/opensre/pull/4597",
    base_branch="main",
    head_branch="feat/fix-ci",
    head_sha="old-sha",
    skipped_check_names=(),
    failing_checks=(
        FailingCheck(
            name="quality",
            conclusion="failure",
            details_url="",
            workflow_name="CI",
        ),
    ),
    task="Fix CI.",
)


def _rollup(*, sha: str, name: str, conclusion: str, status: str) -> dict:
    return {
        "headRefOid": sha,
        "statusCheckRollup": [
            {
                "name": name,
                "conclusion": conclusion,
                "status": status,
            }
        ],
    }


def test_wait_for_pr_checks_ignores_stale_checks_then_waits_for_success() -> None:
    responses = [
        _rollup(sha="old-sha", name="quality", conclusion="FAILURE", status="COMPLETED"),
        _rollup(sha="new-sha", name="quality", conclusion="", status="IN_PROGRESS"),
        _rollup(sha="new-sha", name="quality", conclusion="SUCCESS", status="COMPLETED"),
    ]
    sleeps: list[float] = []

    with patch(
        "integrations.github.tools.ci_fix.verification.run_gh_json",
        side_effect=responses,
    ):
        result = wait_for_pr_checks(
            _CONTEXT,
            github_token="tok",
            timeout_seconds=30,
            poll_interval_seconds=1,
            settle_seconds=0,
            sleep=sleeps.append,
        )

    assert result.state is CheckState.PASSED
    assert result.check_names == ("quality",)
    assert sleeps == [1, 1]


def test_wait_for_pr_checks_reports_terminal_failure() -> None:
    payload = _rollup(
        sha="new-sha",
        name="test (integrations-and-misc)",
        conclusion="FAILURE",
        status="COMPLETED",
    )

    with patch(
        "integrations.github.tools.ci_fix.verification.run_gh_json",
        return_value=payload,
    ):
        result = wait_for_pr_checks(_CONTEXT, github_token="tok", settle_seconds=0)

    assert result.state is CheckState.FAILED
    assert result.failing_checks == ("test (integrations-and-misc)",)


def test_wait_for_pr_checks_waits_for_terminal_rollup_to_settle() -> None:
    responses = [
        _rollup(sha="new-sha", name="quality", conclusion="SUCCESS", status="COMPLETED"),
        {
            "headRefOid": "new-sha",
            "statusCheckRollup": [
                {
                    "name": "quality",
                    "conclusion": "SUCCESS",
                    "status": "COMPLETED",
                },
                {
                    "name": "tests",
                    "conclusion": "SUCCESS",
                    "status": "COMPLETED",
                },
            ],
        },
        {
            "headRefOid": "new-sha",
            "statusCheckRollup": [
                {
                    "name": "quality",
                    "conclusion": "SUCCESS",
                    "status": "COMPLETED",
                },
                {
                    "name": "tests",
                    "conclusion": "SUCCESS",
                    "status": "COMPLETED",
                },
            ],
        },
    ]
    sleeps: list[float] = []
    elapsed = iter((0.0, 0.0, 1.0, 3.0))

    with patch(
        "integrations.github.tools.ci_fix.verification.run_gh_json",
        side_effect=responses,
    ):
        result = wait_for_pr_checks(
            _CONTEXT,
            github_token="tok",
            settle_seconds=2,
            sleep=sleeps.append,
            monotonic=lambda: next(elapsed),
        )

    assert result.state is CheckState.PASSED
    assert result.check_names == ("quality", "tests")
    assert sleeps == [DEFAULT_POLL_INTERVAL_SECONDS, DEFAULT_POLL_INTERVAL_SECONDS]


def test_wait_for_pr_checks_accepts_check_that_was_already_skipped() -> None:
    context = replace(
        _CONTEXT,
        skipped_check_names=("windows test",),
    )
    payload = {
        "headRefOid": "new-sha",
        "statusCheckRollup": [
            {"name": "quality", "conclusion": "SUCCESS", "status": "COMPLETED"},
            {"name": "windows test", "conclusion": "SKIPPED", "status": "COMPLETED"},
        ],
    }

    with patch(
        "integrations.github.tools.ci_fix.verification.run_gh_json",
        return_value=payload,
    ):
        result = wait_for_pr_checks(context, github_token="tok", settle_seconds=0)

    assert result.state is CheckState.PASSED
    assert result.failing_checks == ()


def test_wait_for_pr_checks_rejects_newly_skipped_check() -> None:
    payload = _rollup(
        sha="new-sha",
        name="quality",
        conclusion="SKIPPED",
        status="COMPLETED",
    )

    with patch(
        "integrations.github.tools.ci_fix.verification.run_gh_json",
        return_value=payload,
    ):
        result = wait_for_pr_checks(_CONTEXT, github_token="tok", settle_seconds=0)

    assert result.state is CheckState.FAILED
    assert result.failing_checks == ("quality",)


def test_wait_for_pr_checks_times_out_when_new_checks_never_appear() -> None:
    payload = {"headRefOid": "old-sha", "statusCheckRollup": []}
    elapsed = iter((0.0, 0.0, 10.0))

    with patch(
        "integrations.github.tools.ci_fix.verification.run_gh_json",
        return_value=payload,
    ):
        result = wait_for_pr_checks(
            _CONTEXT,
            github_token="tok",
            timeout_seconds=10,
            poll_interval_seconds=1,
            sleep=lambda _seconds: None,
            monotonic=lambda: next(elapsed),
        )

    assert result.state is CheckState.TIMED_OUT
    assert result.check_names == ()
