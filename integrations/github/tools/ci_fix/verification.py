"""Wait for the checks triggered by a pushed CI fix."""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from integrations.github.tools.ci_fix.context import CiFixContext
from integrations.github.tools.ci_fix.gh import run_gh_json

DEFAULT_CHECK_WAIT_SECONDS = 900
DEFAULT_REGISTRATION_SECONDS = 60
DEFAULT_POLL_INTERVAL_SECONDS = 10
DEFAULT_SETTLE_SECONDS = 30
DEFAULT_HEAD_PROPAGATION_SECONDS = 30

_PR_CHECK_FIELDS = "headRefOid,statusCheckRollup"
_WORKFLOW_RUN_FIELDS = "databaseId,status"
_WORKFLOW_RUNS_KEY = "runs"
_WORKFLOW_RUN_COMPLETED = "completed"
_FAILED_CONCLUSIONS = frozenset(
    {
        "ACTION_REQUIRED",
        "CANCELLED",
        "FAILURE",
        "STALE",
        "STARTUP_FAILURE",
        "TIMED_OUT",
    }
)
_FAILED_STATES = frozenset({"ERROR", "FAILURE", "FAILED"})
_PASSED_CONCLUSIONS = frozenset({"NEUTRAL", "SUCCESS"})
_PASSED_STATES = frozenset({"SUCCESS"})
_SKIPPED_CONCLUSION = "SKIPPED"


class CheckState(StrEnum):
    """Terminal outcome of the checks triggered by the fix push."""

    PASSED = "passed"
    FAILED = "failed"
    TIMED_OUT = "timed_out"
    SUPERSEDED = "superseded"


@dataclass(frozen=True)
class CheckVerification:
    """Observed post-push PR check outcome."""

    state: CheckState
    check_names: tuple[str, ...]
    failing_checks: tuple[str, ...] = ()
    observed_head_sha: str = ""


def wait_for_pr_checks(
    ctx: CiFixContext,
    *,
    github_token: str | None,
    expected_head_sha: str,
    timeout_seconds: int = DEFAULT_CHECK_WAIT_SECONDS,
    poll_interval_seconds: int = DEFAULT_POLL_INTERVAL_SECONDS,
    settle_seconds: int = DEFAULT_SETTLE_SECONDS,
    registration_seconds: int = DEFAULT_REGISTRATION_SECONDS,
    head_propagation_seconds: int = DEFAULT_HEAD_PROPAGATION_SECONDS,
    sleep: Callable[[float], None] = time.sleep,
    monotonic: Callable[[], float] = time.monotonic,
) -> CheckVerification:
    """Poll until the fix commit's PR checks pass, fail, or time out."""
    repo = f"{ctx.owner}/{ctx.repo}"
    started_at = monotonic()
    deadline = started_at + max(0, timeout_seconds)
    expected_skips = set(ctx.skipped_check_names)
    actions_run_required = any(check.run_id or check.workflow_name for check in ctx.failing_checks)
    required_external_checks = {
        check.name for check in ctx.failing_checks if not check.run_id and not check.workflow_name
    }
    last_names: tuple[str, ...] = ()
    terminal_signature: tuple[str, ...] = ()
    terminal_since: float | None = None
    expected_head_seen_at: float | None = None

    while True:
        payload = run_gh_json(
            ["pr", "view", str(ctx.number), "--json", _PR_CHECK_FIELDS],
            repo=repo,
            github_token=github_token,
        )
        head_sha = str(payload.get("headRefOid") or "").strip()
        checks = _check_rows(payload.get("statusCheckRollup"))
        last_names = tuple(_check_name(check) for check in checks)
        now = monotonic()

        if head_sha == expected_head_sha:
            if expected_head_seen_at is None:
                expected_head_seen_at = now
        elif head_sha and (
            head_sha != ctx.head_sha
            or expected_head_seen_at is not None
            or now - started_at >= max(0, head_propagation_seconds)
        ):
            return CheckVerification(
                state=CheckState.SUPERSEDED,
                check_names=last_names,
                observed_head_sha=head_sha,
            )

        # Give the exact commit's checks time to register, then settle only
        # completed workflows. Workflow identities join the signature below so
        # another run appearing during settlement resets the clock.
        if head_sha == expected_head_sha:
            workflows_complete, workflow_signature = _workflow_runs_state(
                repo=repo,
                github_token=github_token,
                expected_head_sha=expected_head_sha,
                require_run=actions_run_required,
            )
        else:
            workflows_complete, workflow_signature = False, ()
        external_checks_registered = required_external_checks.issubset(set(last_names))
        registration_complete = (
            expected_head_seen_at is not None
            and now - expected_head_seen_at >= max(0, registration_seconds)
        )
        if (
            head_sha == expected_head_sha
            and checks
            and workflows_complete
            and external_checks_registered
            and registration_complete
        ):
            pending = [check for check in checks if not _check_is_terminal(check)]
            if not pending:
                signature = _verification_signature(checks, workflow_signature)
                if signature != terminal_signature:
                    terminal_signature = signature
                    terminal_since = now
                if terminal_since is not None and now - terminal_since >= max(0, settle_seconds):
                    failing = tuple(
                        _check_name(check)
                        for check in checks
                        if _check_failed(check, expected_skips=expected_skips)
                    )
                    return CheckVerification(
                        state=CheckState.FAILED if failing else CheckState.PASSED,
                        check_names=last_names,
                        failing_checks=failing,
                    )
            else:
                terminal_signature = ()
                terminal_since = None
        else:
            terminal_signature = ()
            terminal_since = None

        if now >= deadline:
            return CheckVerification(state=CheckState.TIMED_OUT, check_names=last_names)
        sleep(max(0, poll_interval_seconds))


def _check_rows(value: Any) -> list[dict[str, Any]]:
    return [item for item in value if isinstance(item, dict)] if isinstance(value, list) else []


def _check_name(check: dict[str, Any]) -> str:
    return str(check.get("name") or check.get("context") or "unnamed check")


def _workflow_runs_state(
    *,
    repo: str,
    github_token: str | None,
    expected_head_sha: str,
    require_run: bool,
) -> tuple[bool, tuple[str, ...]]:
    payload = run_gh_json(
        [
            "run",
            "list",
            "--commit",
            expected_head_sha,
            "--limit",
            "100",
            "--json",
            _WORKFLOW_RUN_FIELDS,
            "--jq",
            f'{{"{_WORKFLOW_RUNS_KEY}": .}}',
        ],
        repo=repo,
        github_token=github_token,
    )
    runs = _check_rows(payload.get(_WORKFLOW_RUNS_KEY))
    complete = (bool(runs) or not require_run) and all(
        str(run.get("status") or "").strip().lower() == _WORKFLOW_RUN_COMPLETED for run in runs
    )
    signature = tuple(
        sorted(
            f"{run.get('databaseId') or 'unknown'}:{str(run.get('status') or '').strip().lower()}"
            for run in runs
        )
    )
    return complete, signature


def _verification_signature(
    checks: list[dict[str, Any]],
    workflow_signature: tuple[str, ...],
) -> tuple[str, ...]:
    return (*_check_signature(checks), *(f"workflow:{item}" for item in workflow_signature))


def _check_signature(checks: list[dict[str, Any]]) -> tuple[str, ...]:
    signatures = (
        "\x1f".join(
            (
                _check_name(check),
                str(check.get("status") or ""),
                str(check.get("conclusion") or ""),
                str(check.get("state") or ""),
            )
        )
        for check in checks
    )
    return tuple(sorted(signatures))


def _check_failed(check: dict[str, Any], *, expected_skips: set[str]) -> bool:
    conclusion = str(check.get("conclusion") or "").strip().upper()
    state = str(check.get("state") or "").strip().upper()
    if conclusion == _SKIPPED_CONCLUSION:
        return _check_name(check) not in expected_skips
    return conclusion in _FAILED_CONCLUSIONS or state in _FAILED_STATES


def _check_is_terminal(check: dict[str, Any]) -> bool:
    conclusion = str(check.get("conclusion") or "").strip().upper()
    state = str(check.get("state") or "").strip().upper()
    return (
        conclusion in _FAILED_CONCLUSIONS
        or conclusion in _PASSED_CONCLUSIONS
        or conclusion == _SKIPPED_CONCLUSION
        or state in _FAILED_STATES
        or state in _PASSED_STATES
    )


__all__ = [
    "CheckState",
    "CheckVerification",
    "DEFAULT_CHECK_WAIT_SECONDS",
    "DEFAULT_HEAD_PROPAGATION_SECONDS",
    "DEFAULT_POLL_INTERVAL_SECONDS",
    "DEFAULT_REGISTRATION_SECONDS",
    "DEFAULT_SETTLE_SECONDS",
    "wait_for_pr_checks",
]
