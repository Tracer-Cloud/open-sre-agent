"""Agent-callable authenticated GitHub CLI tool."""

from __future__ import annotations

from typing import Any

from core.tool_framework.tool_decorator import tool
from integrations.github.client import resolve_github_token
from integrations.github.helpers import github_creds, github_source_available
from tools.github_cli.classify import classify_gh_args
from tools.github_cli.runner import run_gh

_ARGS_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "args": {
            "type": "array",
            "items": {"type": "string"},
            "description": (
                "Arguments after the `gh` binary (for example: "
                '["issue", "create", "--title", "Bug", "--body", "…"] or '
                '["issue", "list", "--limit", "10"]). Do not include `gh` itself.'
            ),
        },
        "repo": {
            "type": "string",
            "description": "Optional owner/name passed to gh as -R (overrides default repo).",
        },
        "timeout": {
            "type": "integer",
            "description": "Maximum seconds to wait for gh (default 60, max 120).",
        },
        "github_token": {
            "type": "string",
            "description": "Optional GitHub token override; prefer configured integration/env.",
        },
    },
    "required": ["args"],
}


def _github_cli_available(sources: dict[str, dict]) -> bool:
    gh = sources.get("github", {})
    return bool(
        github_source_available(sources) or resolve_github_token(None) or gh.get("github_token")
    )


def _github_cli_extract_params(sources: dict[str, dict]) -> dict[str, Any]:
    gh = sources.get("github", {})
    params: dict[str, Any] = {}
    if gh:
        creds = github_creds(gh)
        if creds.get("github_token"):
            params["github_token"] = creds["github_token"]
        owner = str(gh.get("owner") or "").strip()
        repo = str(gh.get("repo") or "").strip()
        if owner and repo and "repo" not in params:
            params["repo"] = f"{owner}/{repo}"
    return params


def _normalize_args(args: list[str] | None) -> list[str]:
    if not args:
        return []
    return [str(a) for a in args]


@tool(
    name="github_cli",
    source="github",
    description=(
        "Run GitHub CLI (`gh`) with OpenSRE-configured auth — reads and writes. "
        "Use for issue/PR create, list, view, assign, label, merge, repo list, "
        "and gh api. Prefer this over shell_run / !gh / raw gh. "
        "Pass args after the gh binary; optional repo as owner/name for -R."
    ),
    use_cases=[
        "Creating a GitHub issue (title/body/assignee/labels) when the user asks",
        "Listing or viewing GitHub issues and pull requests via gh",
        "Inspecting repository metadata or listing accessible repos",
        "Editing, closing, commenting, or merging via gh",
    ],
    anti_examples=[
        "Running gh via shell_run or !gh",
        "Printing or logging the GitHub token",
        "Inventing repo lists without calling github_cli",
    ],
    surfaces=("investigation", "chat", "action"),
    side_effect_level="mutating",
    requires_approval=False,
    input_schema=_ARGS_SCHEMA,
    is_available=_github_cli_available,
    extract_params=_github_cli_extract_params,
)
def github_cli(
    args: list[str],
    repo: str | None = None,
    timeout: int | None = None,
    github_token: str | None = None,
    **_kwargs: Any,
) -> dict[str, Any]:
    """Run an authenticated ``gh`` command (read or write; no approval gate)."""
    normalized = _normalize_args(args)
    effect = classify_gh_args(normalized)
    result = run_gh(args=normalized, repo=repo, github_token=github_token, timeout=timeout)
    result["effect"] = effect
    result["tool"] = "github_cli"
    return result


__all__ = ["github_cli"]
