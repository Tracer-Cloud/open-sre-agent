"""Open a GitHub pull request for a shipped Sentry fix.

Resolves the workspace's ``owner/repo`` from its ``origin`` remote and calls the
GitHub REST API (reusing :class:`integrations.github.GitHubRestClient`) to open a
PR from the freshly pushed feature branch into the repo's base branch. The token
is resolved from ``GITHUB_TOKEN``/``GH_TOKEN`` and never appears in results, logs,
or the PR body.
"""

from __future__ import annotations

from dataclasses import dataclass

from integrations.github.client import GitHubApiError, GitHubRestClient, resolve_github_token
from integrations.github.repo_scope import detect_git_remote_repo_scope
from tools.cross_vendor.fix_sentry_issue.errors import (
    ERR_GITHUB_TOKEN,
    ERR_PR_FAILED,
    FixIssueError,
)


@dataclass(frozen=True)
class PullRequest:
    """Identity of an opened pull request."""

    url: str
    number: int


def resolve_repo_scope(workspace: str) -> tuple[str, str]:
    """Return ``(owner, repo)`` for *workspace*'s origin remote, or raise."""
    scope = detect_git_remote_repo_scope(workspace)
    if scope is None:
        raise FixIssueError(
            ERR_PR_FAILED,
            "Could not determine the GitHub owner/repo from the workspace's 'origin' remote.",
        )
    return scope


def open_pull_request(
    workspace: str,
    *,
    head_branch: str,
    base_branch: str,
    title: str,
    body: str,
    github_token: str | None = None,
) -> PullRequest:
    """Open a PR from *head_branch* into *base_branch*. Raises FixIssueError."""
    token = resolve_github_token(github_token)
    if not token:
        raise FixIssueError(
            ERR_GITHUB_TOKEN,
            "A GitHub token is required to open a PR. Set GITHUB_TOKEN or GH_TOKEN.",
        )

    owner, repo = resolve_repo_scope(workspace)
    client = GitHubRestClient(token)
    try:
        payload = client.request(
            "POST",
            f"repos/{owner}/{repo}/pulls",
            body={
                "title": title,
                "head": head_branch,
                "base": base_branch,
                "body": body,
                "maintainer_can_modify": True,
            },
        )
    except GitHubApiError as exc:
        raise FixIssueError(ERR_PR_FAILED, f"GitHub rejected the pull request: {exc}") from exc

    if not isinstance(payload, dict):
        raise FixIssueError(ERR_PR_FAILED, "Unexpected response shape when opening the PR.")

    url = str(payload.get("html_url") or "")
    number_raw = payload.get("number")
    number = int(number_raw) if isinstance(number_raw, int) else 0
    if not url:
        raise FixIssueError(ERR_PR_FAILED, "GitHub did not return a pull request URL.")
    return PullRequest(url=url, number=number)
