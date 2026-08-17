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
DEFAULT_POLL_INTERVAL_SECONDS = 10
DEFAULT_SETTLE_SECONDS = 30

_PR_CHECK_FIELDS = "headRefOid,statusCheckRollup"
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


@dataclass(frozen=True)
class CheckVerification:
    """Observed post-push PR check outcome."""

    state: CheckState
    check_names: tuple[str, ...]
    failing_checks: tuple[str, ...] = ()


def wait_for_pr_checks(
    ctx: CiFixContext,
    *,
    github_token: str | None,
    timeout_seconds: int = DEFAULT_CHECK_WAIT_SECONDS,
    poll_interval_seconds: int = DEFAULT_POLL_INTERVAL_SECONDS,
    settle_seconds: int = DEFAULT_SETTLE_SECONDS,
    sleep: Callable[[float], None] = time.sleep,
    monotonic: Callable[[], float] = time.monotonic,
) -> CheckVerification:
    """Poll until the fix commit's PR checks pass, fail, or time out."""
    repo = f"{ctx.owner}/{ctx.repo}"
    deadline = monotonic() + max(0, timeout_seconds)
    expected_skips = set(ctx.skipped_check_names)
    last_names: tuple[str, ...] = ()
    terminal_signature: tuple[str, ...] = ()
    terminal_since: float | None = None

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

        # GitHub can briefly return the pre-push rollup. It must never make the
        # fresh fix look failed before the new head commit and its checks appear.
        if head_sha and head_sha != ctx.head_sha and checks:
            pending = [check for check in checks if not _check_is_terminal(check)]
            if not pending:
                signature = _check_signature(checks)
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
    "DEFAULT_POLL_INTERVAL_SECONDS",
    "DEFAULT_SETTLE_SECONDS",
    "wait_for_pr_checks",
]
