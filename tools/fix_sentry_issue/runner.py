"""Lifecycle/orchestration for the Sentry issue-fix tool.

Thin free functions the tool's ``run`` drives: opt-in gate, Pi CLI readiness, the
Pi coding run (reusing ``integrations/pi``), and result shaping. No shipping here —
this PR returns the diff for review; commit/PR is a follow-up.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from typing import Any, Final

from integrations.pi import (
    PiCodingResult,
    pi_coding_model,
    pi_coding_timeout_seconds,
    pi_coding_workspace,
    run_pi_coding_task,
    verify_pi_coding,
)
from tools.fix_sentry_issue.context import IssueContext
from tools.fix_sentry_issue.errors import (
    ERR_CLI_UNAVAILABLE,
    ERR_DISABLED,
    ERR_EXECUTION,
    ERR_TIMEOUT,
    FixIssueError,
)

SOURCE: Final = "sentry"
_TRUTHY = {"1", "true", "yes", "on"}
_INSTALL_HINT = "npm i -g @earendil-works/pi-coding-agent"


def is_issue_fix_enabled(env: Mapping[str, str] | None = None) -> bool:
    """Whether the Sentry issue-fix tool is opted in via ``PI_ISSUE_FIX_ENABLED``."""
    source = env if env is not None else os.environ
    return source.get("PI_ISSUE_FIX_ENABLED", "").strip().lower() in _TRUTHY


def ensure_enabled() -> None:
    if not is_issue_fix_enabled():
        raise FixIssueError(
            ERR_DISABLED,
            "Sentry issue-fix tool is disabled. Set PI_ISSUE_FIX_ENABLED=1 "
            "(plus Sentry config and the Pi CLI) to enable it.",
        )


def ensure_cli_ready() -> None:
    available, detail = verify_pi_coding()
    if not available:
        raise FixIssueError(
            ERR_CLI_UNAVAILABLE, f"Pi CLI is not ready: {detail}. Install with: {_INSTALL_HINT}"
        )


def run_fix(ctx: IssueContext, workspace: str | None, model: str | None) -> PiCodingResult:
    return run_pi_coding_task(
        ctx.task,
        workspace=workspace or pi_coding_workspace(),
        model=model or pi_coding_model(),
        timeout_sec=pi_coding_timeout_seconds(),
    )


def to_output(issue_id: str, result: PiCodingResult) -> dict[str, Any]:
    error_kind: str | None = None
    if not result.success:
        error_kind = ERR_TIMEOUT if result.timed_out else ERR_EXECUTION
    return {
        "source": SOURCE,
        "success": result.success,
        "error_kind": error_kind,
        "issue_id": issue_id,
        "summary": result.summary,
        "changed_files": result.changed_files,
        "diff": result.diff,
        "diff_truncated": result.diff_truncated,
        "error": result.error,
    }


def error_output(kind: str, message: str, issue_id: str = "") -> dict[str, Any]:
    return {
        "source": SOURCE,
        "success": False,
        "error_kind": kind,
        "issue_id": issue_id,
        "error": message,
    }
