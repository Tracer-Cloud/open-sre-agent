"""Open GitHub pull requests.

:func:`create_pull_request` is the REST call on its own: give it an owner/repo
and two branch names and it opens the PR. :func:`open_pull_request` wraps it for
callers that have a local checkout, resolving the target repository from the
workspace's ``origin`` remote first — the workspace is the only part that needs
``git``, which is why the two are separate.

Tokens are resolved through the existing GitHub credential helper and never
appear in the returned payload.
"""

from __future__ import annotations

from dataclasses import dataclass

from integrations.github.client import GitHubApiError, GitHubRestClient, resolve_github_token
from integrations.github.path_safety import repo_path
from integrations.github.repo_scope import detect_git_remote_repo_scope
from integrations.github.write_errors import GitHubWriteError

ERR_GITHUB_TOKEN = "github_token_missing"
ERR_REPO_SCOPE = "repo_scope_unresolved"
ERR_PR_FAILED = "pr_failed"


@dataclass(frozen=True)
class PullRequest:
    """Identity of an opened pull request."""

    url: str
    number: int


class GitHubPullRequestError(GitHubWriteError):
    """Expected PR-open failure with a stable ``kind`` for callers to map."""


def resolve_repo_scope(workspace: str) -> tuple[str, str]:
    """Return ``(owner, repo)`` for *workspace*'s origin remote, or raise."""
    scope = detect_git_remote_repo_scope(workspace)
    if scope is None:
        raise GitHubPullRequestError(
            ERR_REPO_SCOPE,
            "Could not determine the GitHub owner/repo from the workspace's 'origin' remote.",
        )
    return scope


def create_pull_request(
    client: GitHubRestClient,
    *,
    owner: str,
    repo: str,
    head_branch: str,
    base_branch: str,
    title: str,
    body: str,
    draft: bool = False,
) -> PullRequest:
    """Open a PR from *head_branch* into *base_branch* on an explicit repo.

    Needs no local checkout — both branches must already exist on the remote.
    """
    try:
        payload = client.request(
            "POST",
            repo_path(owner, repo, "pulls"),
            body={
                "title": title,
                "head": head_branch,
                "base": base_branch,
                "body": body,
                "draft": draft,
                "maintainer_can_modify": True,
            },
        )
    except GitHubApiError as exc:
        raise GitHubPullRequestError(
            ERR_PR_FAILED, f"GitHub rejected the pull request: {exc}"
        ) from exc

    if not isinstance(payload, dict):
        raise GitHubPullRequestError(
            ERR_PR_FAILED, "Unexpected response shape when opening the PR."
        )

    url = str(payload.get("html_url") or "")
    number_raw = payload.get("number")
    number = int(number_raw) if isinstance(number_raw, int) else 0
    if not url:
        raise GitHubPullRequestError(ERR_PR_FAILED, "GitHub did not return a pull request URL.")
    return PullRequest(url=url, number=number)


def open_pull_request(
    workspace: str,
    *,
    head_branch: str,
    base_branch: str,
    title: str,
    body: str,
    github_token: str | None = None,
) -> PullRequest:
    """Open a PR from *head_branch* into *base_branch* for a local workspace."""
    token = resolve_github_token(github_token)
    if not token:
        raise GitHubPullRequestError(
            ERR_GITHUB_TOKEN,
            "A GitHub token is required to open a PR. Set GITHUB_TOKEN or GH_TOKEN.",
        )

    owner, repo = resolve_repo_scope(workspace)
    return create_pull_request(
        GitHubRestClient(token),
        owner=owner,
        repo=repo,
        head_branch=head_branch,
        base_branch=base_branch,
        title=title,
        body=body,
    )


__all__ = [
    "ERR_GITHUB_TOKEN",
    "ERR_PR_FAILED",
    "ERR_REPO_SCOPE",
    "GitHubPullRequestError",
    "PullRequest",
    "create_pull_request",
    "open_pull_request",
    "resolve_repo_scope",
]
