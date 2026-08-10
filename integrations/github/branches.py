"""Create a branch carrying file edits, with no clone and no ``git`` binary.

The Git Data API is the only write path open to a process that has neither
``git`` nor a checkout — the gateway container has neither. Blob contents are
described inline, hung off the base commit's tree, committed, and pointed at by
a brand-new ref. One call produces exactly one commit, so a multi-file fix lands
atomically and a failure part-way through leaves no branch behind.

Every path here *creates* ``refs/heads/<new_branch>`` or fails. Nothing updates a
ref that already exists, so this module cannot move the base branch however it is
called — the only way a change reaches ``main`` is a human merging the pull
request.
"""

from __future__ import annotations

from dataclasses import dataclass
from http import HTTPStatus
from typing import Any

from integrations.github.client import GitHubApiError, GitHubRestClient
from integrations.github.path_safety import (
    invalid_branch_reason,
    repo_path,
    safe_git_ref,
)
from integrations.github.write_errors import GitHubWriteError

ERR_INVALID_BRANCH = "invalid_branch_name"
ERR_BASE_BRANCH = "base_branch_not_found"
ERR_BRANCH_EXISTS = "branch_exists"
ERR_NO_CHANGES = "no_changes"
ERR_COMMIT_FAILED = "commit_failed"

# Regular, non-executable file. The Git Data API wants a mode on every tree
# entry; this path never creates symlinks, submodules, or executables.
_BLOB_MODE = "100644"


@dataclass(frozen=True)
class FileChange:
    """One file to write, or to delete when ``content`` is ``None``.

    ``content`` is the file's **complete** new text, not a patch: the Git Data
    API replaces the blob wholesale. Text only — the inline ``content`` field is
    UTF-8, so binary files need a different path.
    """

    path: str
    content: str | None


class GitHubBranchError(GitHubWriteError):
    """Expected branch-creation failure with a stable ``kind``."""


def _tree_entry(change: FileChange) -> dict[str, Any]:
    if change.content is None:
        # A null sha is how the Git Data API spells "drop this path from the
        # tree". An empty content string would create an empty file instead.
        return {"path": change.path, "mode": _BLOB_MODE, "type": "blob", "sha": None}
    return {
        "path": change.path,
        "mode": _BLOB_MODE,
        "type": "blob",
        "content": change.content,
    }


def _as_dict(payload: Any, *, step: str) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise GitHubBranchError(
            ERR_COMMIT_FAILED, f"Unexpected response shape from GitHub ({step})."
        )
    return payload


def _validate_changes(changes: list[FileChange]) -> None:
    if not changes:
        raise GitHubBranchError(ERR_NO_CHANGES, "No file changes were supplied.")
    seen: set[str] = set()
    for change in changes:
        path = change.path.strip()
        if not path or path.startswith("/") or ".." in path.split("/"):
            raise GitHubBranchError(
                ERR_NO_CHANGES,
                f"File path '{change.path}' must be repository-relative and must not escape the tree.",
            )
        if path in seen:
            raise GitHubBranchError(
                ERR_NO_CHANGES, f"File path '{path}' appears more than once in the change set."
            )
        seen.add(path)


def branch_exists(client: GitHubRestClient, *, owner: str, repo: str, branch: str) -> bool:
    """Return True when ``refs/heads/<branch>`` already exists in the repo."""
    safe_branch = safe_git_ref(branch)
    if safe_branch is None:
        raise GitHubBranchError(ERR_INVALID_BRANCH, f"Invalid branch name: {branch}")
    # The ref is appended, not passed through repo_path: ``git/ref/<ref>`` takes
    # a multi-segment ref, so a branch like ``feat/foo`` must keep its ``/``.
    try:
        client.request("GET", f"{repo_path(owner, repo, 'git', 'ref')}/heads/{safe_branch}")
    except GitHubApiError as exc:
        if exc.status_code == HTTPStatus.NOT_FOUND:
            return False
        raise
    return True


def _base_commit(
    client: GitHubRestClient, *, owner: str, repo: str, base_branch: str
) -> tuple[str, str]:
    """Return ``(commit_sha, tree_sha)`` for the tip of *base_branch*."""
    safe_base_branch = safe_git_ref(base_branch)
    if safe_base_branch is None:
        raise GitHubBranchError(ERR_BASE_BRANCH, f"Invalid base branch name: {base_branch}")
    try:
        ref = client.request(
            "GET", f"{repo_path(owner, repo, 'git', 'ref')}/heads/{safe_base_branch}"
        )
    except GitHubApiError as exc:
        if exc.status_code == HTTPStatus.NOT_FOUND:
            raise GitHubBranchError(
                ERR_BASE_BRANCH,
                f"Base branch '{base_branch}' does not exist in {owner}/{repo}.",
            ) from exc
        raise GitHubBranchError(
            ERR_COMMIT_FAILED, f"Could not read base branch '{base_branch}': {exc}"
        ) from exc

    commit_sha = str(_as_dict(ref, step="read base ref").get("object", {}).get("sha") or "")
    if not commit_sha:
        raise GitHubBranchError(
            ERR_BASE_BRANCH, f"Base branch '{base_branch}' does not point at a commit."
        )
    try:
        commit = client.request("GET", repo_path(owner, repo, "git", "commits", commit_sha))
    except GitHubApiError as exc:
        raise GitHubBranchError(
            ERR_COMMIT_FAILED, f"Could not read the tip commit of '{base_branch}': {exc}"
        ) from exc
    tree = _as_dict(commit, step="read base commit").get("tree", {})
    tree_sha = str(tree.get("sha") or "") if isinstance(tree, dict) else ""
    if not tree_sha:
        raise GitHubBranchError(
            ERR_COMMIT_FAILED, f"The tip commit of '{base_branch}' has no tree."
        )
    return commit_sha, tree_sha


def create_branch_with_changes(
    client: GitHubRestClient,
    *,
    owner: str,
    repo: str,
    base_branch: str,
    new_branch: str,
    changes: list[FileChange],
    commit_message: str,
) -> str:
    """Create *new_branch* off *base_branch* with *changes* as one commit.

    Returns the new commit's sha. Raises :class:`GitHubBranchError` when the
    branch name is unusable, the base branch is missing, the new branch already
    exists, the change set is empty or malformed, or GitHub rejects the write.
    """
    invalid = invalid_branch_reason(new_branch)
    if invalid is not None:
        raise GitHubBranchError(ERR_INVALID_BRANCH, f"Cannot create branch: {invalid}.")
    if new_branch == base_branch:
        raise GitHubBranchError(
            ERR_INVALID_BRANCH,
            f"The new branch must differ from the base branch ('{base_branch}'); "
            "this tool never commits to the base branch.",
        )
    _validate_changes(changes)

    if branch_exists(client, owner=owner, repo=repo, branch=new_branch):
        raise GitHubBranchError(
            ERR_BRANCH_EXISTS,
            f"Branch '{new_branch}' already exists in {owner}/{repo}. "
            "Pick a new name; this tool never force-updates an existing branch.",
        )

    base_commit_sha, base_tree_sha = _base_commit(
        client, owner=owner, repo=repo, base_branch=base_branch
    )

    try:
        tree = _as_dict(
            client.request(
                "POST",
                repo_path(owner, repo, "git", "trees"),
                body={
                    "base_tree": base_tree_sha,
                    "tree": [_tree_entry(change) for change in changes],
                },
            ),
            step="create tree",
        )
        commit = _as_dict(
            client.request(
                "POST",
                repo_path(owner, repo, "git", "commits"),
                body={
                    "message": commit_message,
                    "tree": tree.get("sha"),
                    "parents": [base_commit_sha],
                },
            ),
            step="create commit",
        )
        commit_sha = str(commit.get("sha") or "")
        if not commit_sha:
            raise GitHubBranchError(ERR_COMMIT_FAILED, "GitHub did not return a commit sha.")
        client.request(
            "POST",
            repo_path(owner, repo, "git", "refs"),
            # RAW on purpose: this is a JSON body, not a path. Percent-encoding
            # it would create a branch literally named ``feat%2Ffoo``.
            body={"ref": f"refs/heads/{new_branch}", "sha": commit_sha},
        )
    except GitHubApiError as exc:
        raise GitHubBranchError(
            ERR_COMMIT_FAILED, f"GitHub rejected the commit for '{new_branch}': {exc}"
        ) from exc
    return commit_sha


__all__ = [
    "ERR_BASE_BRANCH",
    "ERR_BRANCH_EXISTS",
    "ERR_COMMIT_FAILED",
    "ERR_INVALID_BRANCH",
    "ERR_NO_CHANGES",
    "FileChange",
    "GitHubBranchError",
    "branch_exists",
    "create_branch_with_changes",
]
