"""Action tool for fixing failing GitHub PR CI and pushing the PR branch."""

from __future__ import annotations

from typing import Any

from core.agent_harness.tools.tool_context import action_context_from_agent_context
from core.domain.types.tools import ToolSurface
from core.tool_framework.metadata import SideEffectLevel
from core.tool_framework.tool_decorator import tool
from integrations.github.client import resolve_github_token
from integrations.github.helpers import (
    GITHUB_INJECTED_PARAMS,
    github_creds,
    github_source_available,
)
from integrations.github.tools.ci_fix.runner import run_ci_fix

_INPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "owner": {
            "type": "string",
            "description": "GitHub repository owner. Optional when pr_url or workspace origin provides it.",
        },
        "repo": {
            "type": "string",
            "description": "GitHub repository name. Optional when pr_url or workspace origin provides it.",
        },
        "pr_number": {
            "type": "integer",
            "description": "Pull request number. Optional when pr_url is provided or current branch has a PR.",
        },
        "pr_url": {
            "type": "string",
            "description": "Optional GitHub pull request URL.",
        },
        "workspace": {
            "type": "string",
            "description": "Absolute path to the local checkout to edit. Defaults to CODING_WORKSPACE or cwd.",
        },
        "model": {
            "type": "string",
            "description": "Optional coding-agent model override.",
        },
        "github_token": {
            "type": "string",
            "description": "GitHub token injected from the configured integration.",
        },
    },
    "additionalProperties": False,
}


def _github_ci_fix_available(sources: dict[str, dict]) -> bool:
    gh = sources.get("github", {})
    return bool(
        github_source_available(sources)
        or resolve_github_token(None)
        or github_creds(gh).get("github_token")
    )


def _github_ci_fix_extract_params(sources: dict[str, dict]) -> dict[str, Any]:
    gh = sources.get("github", {})
    if not gh:
        return {}
    params = github_creds(gh)
    owner = str(gh.get("owner") or "").strip()
    repo = str(gh.get("repo") or "").strip()
    if owner:
        params["owner"] = owner
    if repo:
        params["repo"] = repo
    return params


def _confirm_fn(context: Any) -> Any:
    if context is None:
        return None
    try:
        action_context = action_context_from_agent_context(context)
    except RuntimeError:
        return None
    return action_context.confirm_fn


@tool(
    name="fix_github_pr_ci",
    source="github",
    display_name="Fix GitHub PR CI",
    description=(
        "Inspect failing GitHub Actions checks on a pull request, run an "
        "auto-detected coding agent with the failing log context, commit the "
        "result, push it to the pull request's existing head branch, and wait "
        "for the new checks to finish. It does not open a new PR and refuses "
        "fork PR branches."
    ),
    use_cases=[
        "Fix failing CI on a GitHub pull request and push to the PR branch",
        "Fix the CI failure for a PR URL",
        "Repair a failing GitHub Actions check on the current branch PR",
    ],
    anti_examples=[
        "Creating or closing ordinary GitHub issues (use github_cli)",
        "Re-running workflows without code changes",
        "Fixing GitHub security alerts (use fix_github_security_alert)",
    ],
    surfaces=(ToolSurface.ACTION,),
    side_effect_level=SideEffectLevel.MUTATING,
    requires_approval=True,
    approval_reason=(
        "Checks out the PR branch, edits files, commits, pushes to that branch, "
        "and waits for the resulting checks."
    ),
    parallel_safe=False,
    accepts_runtime_context=True,
    input_schema=_INPUT_SCHEMA,
    is_available=_github_ci_fix_available,
    extract_params=_github_ci_fix_extract_params,
    injected_params=GITHUB_INJECTED_PARAMS,
)
def fix_github_pr_ci(
    owner: str | None = None,
    repo: str | None = None,
    pr_number: int | None = None,
    pr_url: str | None = None,
    workspace: str | None = None,
    model: str | None = None,
    github_token: str | None = None,
    context: Any = None,
    **_kwargs: Any,
) -> dict[str, Any]:
    """Run the GitHub PR CI remediation flow."""
    return run_ci_fix(
        owner=owner,
        repo=repo,
        pr_number=pr_number,
        pr_url=pr_url,
        workspace=workspace,
        model=model,
        github_token=github_token,
        confirm_fn=_confirm_fn(context),
    )


__all__ = ["fix_github_pr_ci"]
