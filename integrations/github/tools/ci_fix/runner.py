"""Lifecycle for fixing failing GitHub PR CI and pushing to the PR branch."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any, Final

from integrations.coding_agent import (
    CodingResult,
    coding_model,
    coding_timeout_seconds,
    coding_workspace,
    run_coding_task,
    verify_coding_agent,
)
from integrations.git import GitCommandError, changed_paths, ensure_git_repo, file_fingerprints
from integrations.github.client import resolve_github_token
from integrations.github.repo_scope import detect_git_remote_repo_scope
from integrations.github.tools.ci_fix.context import CiFixContext, gather_ci_fix_context
from integrations.github.tools.ci_fix.errors import (
    ERR_CONFIRMATION_DENIED,
    ERR_EXECUTION,
    ERR_GITHUB_TOKEN,
    ERR_REPO_MISMATCH,
    ERR_REPO_SCOPE,
    ERR_TIMEOUT,
    GitHubCiFixError,
)
from integrations.github.tools.ci_fix.ship import PushResult, checkout_pr_branch, push_ci_fix

SOURCE: Final = "github"
_YES = {"y", "yes"}


def resolve_workspace(workspace: str | None) -> str:
    """Resolve the workspace once for the full run."""
    return workspace or coding_workspace()


def ensure_workspace_ready(workspace: str, owner: str, repo: str) -> None:
    """Require a git checkout whose origin matches the PR repository."""
    try:
        ensure_git_repo(workspace)
    except GitCommandError as exc:
        raise GitHubCiFixError(exc.kind, exc.message) from exc
    detected = detect_git_remote_repo_scope(workspace)
    if detected is None:
        raise GitHubCiFixError(
            ERR_REPO_SCOPE,
            "Could not determine the GitHub owner/repo from the workspace's origin remote; no push was made.",
        )
    detected_owner, detected_repo = detected
    if (detected_owner.lower(), detected_repo.lower()) != (owner.lower(), repo.lower()):
        raise GitHubCiFixError(
            ERR_REPO_MISMATCH,
            (
                f"Workspace origin is {detected_owner}/{detected_repo}, "
                f"but the PR belongs to {owner}/{repo}; no push was made."
            ),
        )


def ensure_push_ready(github_token: str | None = None) -> None:
    if not resolve_github_token(github_token):
        raise GitHubCiFixError(
            ERR_GITHUB_TOKEN,
            "A GitHub token is required to push CI fixes; no push was made.",
        )


def pre_coding_changes(workspace: str) -> dict[str, str]:
    """Fingerprint already-dirty files so pushing excludes untouched WIP."""
    try:
        return file_fingerprints(workspace, changed_paths(workspace))
    except GitCommandError:
        return {}


def run_fix(ctx: CiFixContext, workspace: str, model: str | None) -> CodingResult:
    available, _detail = verify_coding_agent()
    if not available:
        return CodingResult(
            success=False,
            summary="",
            error=(
                f"Found failing CI checks on {ctx.owner}/{ctx.repo}#{ctx.number}, "
                "but no configured coding agent is ready; no push was made."
            ),
            returncode=-1,
        )
    return run_coding_task(
        ctx.task,
        workspace=workspace,
        model=model or coding_model(),
        timeout_sec=coding_timeout_seconds(),
    )


def require_confirmation(
    confirm_fn: Callable[[str], str] | None,
    prompt: str,
) -> None:
    """Ask for local confirmation when a shell confirmation function is present."""
    if confirm_fn is None:
        return
    answer = confirm_fn(prompt)
    if answer.strip().lower() not in _YES:
        raise GitHubCiFixError(ERR_CONFIRMATION_DENIED, "User denied the requested CI fix.")


def _base_output(ctx: CiFixContext | None = None) -> dict[str, Any]:
    return {
        "source": SOURCE,
        "success": False,
        "error_kind": None,
        "owner": ctx.owner if ctx else "",
        "repo": ctx.repo if ctx else "",
        "pr_number": ctx.number if ctx else None,
        "pr_url": ctx.url if ctx else "",
        "base_branch": ctx.base_branch if ctx else "",
        "head_branch": ctx.head_branch if ctx else "",
        "failing_checks": [check.name for check in ctx.failing_checks] if ctx else [],
        "summary": "",
        "response_text": "",
        "changed_files": [],
        "diff": "",
        "diff_truncated": False,
        "error": None,
        "branch_name": None,
    }


def to_output(ctx: CiFixContext, result: CodingResult) -> dict[str, Any]:
    error_kind: str | None = None
    if not result.success:
        error_kind = ERR_TIMEOUT if result.timed_out else ERR_EXECUTION
    return {
        **_base_output(ctx),
        "success": result.success,
        "error_kind": error_kind,
        "summary": result.summary,
        "response_text": _result_response_text(ctx, result),
        "changed_files": result.changed_files,
        "diff": result.diff,
        "diff_truncated": result.diff_truncated,
        "error": result.error,
    }


def with_push_output(output: dict[str, Any], push: PushResult) -> dict[str, Any]:
    owner = str(output.get("owner") or "")
    repo = str(output.get("repo") or "")
    number = output.get("pr_number")
    return {
        **output,
        "branch_name": push.branch_name,
        "changed_files": push.changed_files,
        "response_text": (
            f"Fixed failing CI for {owner}/{repo}#{number} and pushed {push.branch_name}."
        ),
    }


def push_error_output(output: dict[str, Any], exc: GitHubCiFixError) -> dict[str, Any]:
    return {
        **output,
        "success": False,
        "error_kind": exc.kind,
        "error": exc.message,
        "response_text": _single_line(exc.message),
        "branch_name": exc.branch_name,
    }


def error_output(kind: str, message: str, ctx: CiFixContext | None = None) -> dict[str, Any]:
    return {
        **_base_output(ctx),
        "error_kind": kind,
        "error": message,
        "response_text": _single_line(message),
    }


def _result_response_text(ctx: CiFixContext, result: CodingResult) -> str:
    if not result.success:
        return _single_line(result.error or "No CI fix was produced; no push was made.")
    changed = ", ".join(result.changed_files) if result.changed_files else "the workspace"
    return f"Prepared a CI fix for {ctx.owner}/{ctx.repo}#{ctx.number}; changed {changed}."


def _single_line(value: str) -> str:
    return " ".join(value.split())


def run_ci_fix(
    *,
    owner: str | None = None,
    repo: str | None = None,
    pr_number: int | None = None,
    pr_url: str | None = None,
    workspace: str | None = None,
    model: str | None = None,
    github_token: str | None = None,
    confirm_fn: Callable[[str], str] | None = None,
) -> dict[str, Any]:
    ws = resolve_workspace(workspace)
    ctx: CiFixContext | None = None
    try:
        ctx = gather_ci_fix_context(
            owner=owner,
            repo=repo,
            pr_number=pr_number,
            pr_url=pr_url,
            workspace=ws,
            github_token=github_token,
        )
        ensure_workspace_ready(ws, ctx.owner, ctx.repo)
        ensure_push_ready(github_token=github_token)
        require_confirmation(
            confirm_fn,
            (
                f"Fix failing CI for {ctx.owner}/{ctx.repo}#{ctx.number} by checking out "
                f"{ctx.head_branch}, editing files, committing, and pushing to that branch? [y/N] "
            ),
        )
        checkout_pr_branch(ws, ctx)
    except GitHubCiFixError as exc:
        return error_output(exc.kind, exc.message, ctx)

    baseline = pre_coding_changes(ws)
    result = run_fix(ctx, ws, model)
    output = to_output(ctx, result)
    if not result.success:
        return output

    try:
        push = push_ci_fix(
            ctx=ctx, result=result, workspace=ws, baseline=baseline, github_token=github_token
        )
    except GitHubCiFixError as exc:
        return push_error_output(output, exc)
    return with_push_output(output, push)


__all__ = [
    "SOURCE",
    "ensure_push_ready",
    "ensure_workspace_ready",
    "error_output",
    "pre_coding_changes",
    "require_confirmation",
    "resolve_workspace",
    "run_ci_fix",
    "run_fix",
    "with_push_output",
]
