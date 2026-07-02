"""Sentry issue-fix tool: paste a Sentry issue URL and Pi proposes the fix.

PR 1 of the issue-fix flow: resolve the issue from Sentry, run the Pi coding agent
in the **current workspace**, and return a summary + git diff for review. It does
**not** commit, push, or open a PR (that is a follow-up PR).

Package layout (separation of concerns, like ``tools/pi_coding_tool``):

- ``errors.py``    — :class:`FixIssueError` + stable ``error_kind`` constants.
- ``context.py``   — Sentry URL parse + issue fetch, compacted into a masked task.
- ``runner.py``    — opt-in gate, Pi CLI readiness, the Pi run, result shaping.
- ``__init__.py``  — this file: the agent-facing :class:`BaseTool` contract. The
  class lives here because the tool registry discovers instances by
  ``__class__.__module__`` and does not recurse into sub-modules.

Gating mirrors ``pi_coding_task`` (it is the same mutating capability, issue-driven):
``side_effect_level = "mutating"`` and ``is_available`` is True only when
``PI_ISSUE_FIX_ENABLED`` is set, so it is never offered unless the operator opts in.
Secrets (Sentry token) never enter the Pi prompt — the issue context is masked.
"""

from __future__ import annotations

from typing import Any

from core.tool_framework.base import BaseTool
from tools.fix_sentry_issue.context import gather_issue_context
from tools.fix_sentry_issue.errors import FixIssueError
from tools.fix_sentry_issue.runner import (
    SOURCE,
    ensure_cli_ready,
    ensure_enabled,
    error_output,
    is_issue_fix_enabled,
    run_fix,
    to_output,
)


class FixSentryIssueTool(BaseTool):
    """Resolve a Sentry issue and have Pi propose a fix (diff for review)."""

    name = "fix_sentry_issue"
    display_name = "Fix Sentry issue"
    source = SOURCE
    side_effect_level = "mutating"
    surfaces = ("investigation",)
    requires_approval = True
    approval_reason = "Runs the Pi coding agent to edit files based on a Sentry issue."
    description = (
        "Given a Sentry issue URL, fetch the issue context and run the Pi coding agent "
        "(pi.dev) to propose a fix in the current repository, returning a summary plus the "
        "git diff. It does not commit, push, or open a PR. Disabled unless "
        "PI_ISSUE_FIX_ENABLED=1, Sentry is configured, and the Pi CLI is installed."
    )
    use_cases = [
        "A user pastes a Sentry issue link and asks OpenSRE to fix it",
        "Turn a known Sentry error into a reviewable code change",
    ]
    anti_examples = [
        "Investigating an issue without changing code (use the read-only Sentry tools)",
        "Shipping/committing the fix (not supported yet)",
    ]
    input_schema = {
        "type": "object",
        "properties": {
            "sentry_url": {
                "type": "string",
                "description": "URL of the Sentry issue to fix (.../issues/<id>/).",
            },
            "workspace": {
                "type": "string",
                "description": (
                    "Absolute path to the repository to edit. "
                    "Defaults to PI_CODING_WORKSPACE or the current directory."
                ),
                "nullable": True,
            },
            "model": {
                "type": "string",
                "description": "Optional Pi model override (provider/model). Defaults to PI_CODING_MODEL.",
                "nullable": True,
            },
        },
        "required": ["sentry_url"],
    }
    outputs = {
        "success": "True when Pi produced a fix and exited cleanly",
        "error_kind": "Stable failure category (disabled, invalid_input, sentry_unavailable, "
        "issue_not_found, cli_unavailable, timeout, execution_error) or None on success",
        "issue_id": "The resolved Sentry issue id",
        "summary": "Pi's summary of the fix",
        "changed_files": "Files modified in the working tree",
        "diff": "git diff of the proposed fix (truncated if large)",
        "error": "Human-readable error detail when the run failed",
    }

    def is_available(self, _sources: dict[str, dict]) -> bool:
        """Only available when explicitly opted in (cheap flag check)."""
        return is_issue_fix_enabled()

    def run(
        self,
        sentry_url: str,
        workspace: str | None = None,
        model: str | None = None,
    ) -> dict[str, Any]:
        try:
            ensure_enabled()
            ctx = gather_issue_context(sentry_url)
            ensure_cli_ready()
        except FixIssueError as exc:
            return error_output(exc.kind, exc.message)

        # Unexpected exceptions propagate to BaseTool.__call__ (Sentry-reported).
        return to_output(ctx.issue_id, run_fix(ctx, workspace, model))


# Module-level instance so the tool registry auto-discovers it (see tools/registry.py).
fix_sentry_issue = FixSentryIssueTool()
