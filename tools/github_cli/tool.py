"""Agent-callable authenticated GitHub CLI tools."""

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
        "Run a read-only GitHub CLI (`gh`) command with OpenSRE-configured auth. "
        "Use for listing/viewing issues, PRs, repos, search, and GET-style `gh api`. "
        "For mutating commands (create/edit/close/merge/...), use github_cli_write instead. "
        "Never use shell_run with raw gh."
    ),
    use_cases=[
        "Listing or viewing GitHub issues and pull requests via gh",
        "Inspecting repository metadata with gh repo view",
        "Running read-only gh api GET requests",
    ],
    anti_examples=[
        "Creating or editing issues (use github_cli_write)",
        "Running gh via shell_run",
        "Printing or logging the GitHub token",
    ],
    surfaces=("investigation", "chat", "action"),
    side_effect_level="read_only",
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
    """Run a read-only authenticated ``gh`` command."""
    normalized = _normalize_args(args)
    effect = classify_gh_args(normalized)
    if effect != "read":
        return {
            "ok": False,
            "error": (
                "This command looks mutating. Call github_cli_write instead (requires approval)."
            ),
            "error_type": "wrong_tool",
            "effect": effect,
            "args": normalized,
            "suggested_tool": "github_cli_write",
        }
    result = run_gh(args=normalized, repo=repo, github_token=github_token, timeout=timeout)
    result["effect"] = "read"
    result["tool"] = "github_cli"
    return result


@tool(
    name="github_cli_write",
    source="github",
    description=(
        "Run a mutating GitHub CLI (`gh`) command with OpenSRE-configured auth "
        "(issue/PR create, edit, close, merge, labels, etc.). Requires approval. "
        "Prefer github_cli for read-only commands. Never use shell_run with raw gh."
    ),
    use_cases=[
        "Creating a GitHub issue after the user approves",
        "Editing, closing, or commenting on issues via gh",
        "Mutating pull requests (merge, review, edit) via gh",
    ],
    anti_examples=[
        "Read-only listing (use github_cli)",
        "Running gh via shell_run",
        "Mutating GitHub without user approval",
    ],
    surfaces=("chat", "action"),
    side_effect_level="mutating",
    requires_approval=True,
    approval_reason="Runs a mutating GitHub CLI (`gh`) command with your configured token.",
    input_schema=_ARGS_SCHEMA,
    is_available=_github_cli_available,
    extract_params=_github_cli_extract_params,
)
def github_cli_write(
    args: list[str],
    repo: str | None = None,
    timeout: int | None = None,
    github_token: str | None = None,
    **_kwargs: Any,
) -> dict[str, Any]:
    """Run a mutating authenticated ``gh`` command (approval-gated)."""
    normalized = _normalize_args(args)
    effect = classify_gh_args(normalized)
    if effect != "mutate":
        return {
            "ok": False,
            "error": (
                "This command looks read-only. Call github_cli instead (no approval required)."
            ),
            "error_type": "wrong_tool",
            "effect": effect,
            "args": normalized,
            "suggested_tool": "github_cli",
        }
    result = run_gh(args=normalized, repo=repo, github_token=github_token, timeout=timeout)
    result["effect"] = "mutate"
    result["tool"] = "github_cli_write"
    return result


__all__ = ["github_cli", "github_cli_write"]
