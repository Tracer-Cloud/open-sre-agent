"""Action-surface tool: open a GitHub pull request without a clone.

The gateway container ships no ``git`` and no ``gh``, so every existing write
path (``github_cli``, ``fix_github_security_alert``) is unreachable from a chat
turn — the agent could describe a fix but never propose one. This tool closes
that gap over the REST API alone: branch, commit, pull request, three calls, no
working copy.

It is gated twice. ``requires_approval`` puts Approve / Deny buttons in front of
every call, so nothing is written until a human clicks; and the write itself can
only ever create a new branch, so the worst an approved-by-mistake call produces
is a pull request somebody has to merge.
"""

from __future__ import annotations

from typing import Any

from core.tool_framework.tool_decorator import tool
from integrations.github.branches import FileChange, create_branch_with_changes
from integrations.github.client import GitHubRestClient
from integrations.github.pull_requests import create_pull_request
from integrations.github.tools.github_cli.credentials import (
    GITHUB_CLI_INJECTED_PARAMS,
    github_creds,
    github_source_available,
    resolve_github_token,
)
from integrations.github.tools.pull_request_approval import PULL_REQUEST_APPROVAL_DISPLAY
from integrations.github.write_errors import GitHubWriteError

ERR_TOKEN = "github_token_missing"
ERR_INVALID_CHANGES = "invalid_changes"

_DESCRIPTION = (
    "Open a GitHub pull request that applies file changes, using the REST API only "
    "(no git clone and no gh CLI needed). Creates a new branch off the base branch, "
    "commits the supplied files to it as one commit, and opens the PR. Never commits "
    "to the base branch and never force-updates an existing branch. Each call needs "
    "human approval before anything is written. Pass each file's COMPLETE new "
    "contents, not a diff or a patch."
)

# Full-file replacement is the part models get wrong most often, and the failure
# is silent: a unified diff written as `content` commits cleanly and corrupts the
# file. Called out here as well as in the description and the field docs.
_DIFF_ANTI_EXAMPLE = (
    "Passing a unified diff, patch hunk, or partial snippet as a file's content "
    "(read the file first and pass the whole modified text)"
)
_MERGE_ANTI_EXAMPLE = "Merging, approving, or closing a pull request (this tool only opens one)"
_BINARY_ANTI_EXAMPLE = "Committing binary files such as images or archives (UTF-8 text only)"
_DRAFT_ANTI_EXAMPLE = (
    "Opening the pull request as a draft when nobody asked for one (required "
    "status checks do not run on drafts, so the PR looks unverified)"
)


def _pull_request_available(sources: dict[str, dict]) -> bool:
    """Available whenever a GitHub credential can be resolved at all.

    Mirrors ``architecture_clone_repo``: the integration entry is often absent in
    a container where the token arrives through the environment instead.
    """
    return bool(github_source_available(sources) or resolve_github_token(None))


def _pull_request_extract_params(sources: dict[str, dict]) -> dict[str, Any]:
    """Inject the token, and owner/repo as defaults the model may override."""
    gh = sources.get("github", {})
    if not gh:
        return {}
    payload: dict[str, Any] = {**github_creds(gh)}
    if gh.get("owner"):
        payload["owner"] = gh["owner"]
    if gh.get("repo"):
        payload["repo"] = gh["repo"]
    return payload


def _parse_changes(raw_changes: Any) -> list[FileChange]:
    """Turn the model-supplied ``changes`` array into typed file changes."""
    if not isinstance(raw_changes, list) or not raw_changes:
        raise GitHubWriteError(
            ERR_INVALID_CHANGES, "changes must be a non-empty array of {path, content} objects."
        )
    parsed: list[FileChange] = []
    for entry in raw_changes:
        if not isinstance(entry, dict):
            raise GitHubWriteError(
                ERR_INVALID_CHANGES, "Every entry in changes must be an object with a path."
            )
        path = str(entry.get("path") or "").strip()
        if not path:
            raise GitHubWriteError(ERR_INVALID_CHANGES, "Every entry in changes needs a path.")
        if bool(entry.get("delete")):
            parsed.append(FileChange(path=path, content=None))
            continue
        content = entry.get("content")
        if not isinstance(content, str):
            raise GitHubWriteError(
                ERR_INVALID_CHANGES,
                f"changes entry for '{path}' needs a content string "
                "(the file's complete new text), or delete: true.",
            )
        parsed.append(FileChange(path=path, content=content))
    return parsed


def _failure(kind: str, message: str, *, branch: str = "", branch_created: bool = False) -> dict:
    return {
        "ok": False,
        "error_kind": kind,
        "error": message,
        "branch": branch,
        "branch_created": branch_created,
        "pull_request_url": "",
        "pull_request_number": 0,
    }


@tool(
    name="open_github_pull_request",
    source="github",
    description=_DESCRIPTION,
    use_cases=[
        "Proposing a code or config fix the user asked for, as a reviewable pull request",
        "Shipping a remediation found during an investigation without shell or git access",
        "Removing or correcting an infrastructure-as-code resource in a tracked repository",
    ],
    anti_examples=[
        _DIFF_ANTI_EXAMPLE,
        _MERGE_ANTI_EXAMPLE,
        _BINARY_ANTI_EXAMPLE,
        _DRAFT_ANTI_EXAMPLE,
        "Pushing straight to main or any protected branch",
    ],
    requires=["owner", "repo"],
    surfaces=("action",),
    side_effect_level="mutating",
    requires_approval=True,
    approval_reason="Creates a branch, commits file changes, and opens a pull request on GitHub.",
    # Renders the approval prompt as a pull request rather than a field dump;
    # see integrations/github/tools/pull_request_approval.py.
    approval_display=PULL_REQUEST_APPROVAL_DISPLAY,
    input_schema={
        "type": "object",
        "properties": {
            "owner": {"type": "string", "description": "Repository owner or organization."},
            "repo": {"type": "string", "description": "Repository name."},
            "base_branch": {
                "type": "string",
                "description": "Existing branch to open the pull request against, e.g. main.",
            },
            "head_branch": {
                "type": "string",
                "description": (
                    "New branch to create for the change. Must not already exist. "
                    "Use a descriptive prefixed name, e.g. fix/drop-orphan-subscription."
                ),
            },
            "title": {"type": "string", "description": "Pull request title."},
            "body": {
                "type": "string",
                "description": (
                    "Pull request description: what changed, why, and how it was verified."
                ),
            },
            "commit_message": {
                "type": "string",
                "description": "Commit message. Defaults to the pull request title.",
            },
            "draft": {
                "type": "boolean",
                "description": (
                    "Open the pull request as a draft. Defaults to false, and should stay "
                    "false unless the user explicitly asked for a draft: required status "
                    "checks and CI gates do not run on draft pull requests, so the change "
                    "arrives looking unverified and cannot be merged until someone marks "
                    "it ready."
                ),
            },
            "changes": {
                "type": "array",
                "minItems": 1,
                "description": (
                    "Files to write in the commit. Read each file first and pass its "
                    "complete modified contents; this is not a patch format."
                ),
                "items": {
                    "type": "object",
                    "properties": {
                        "path": {
                            "type": "string",
                            "description": "Repository-relative path, e.g. terraform/main.tf.",
                        },
                        "content": {
                            "type": "string",
                            "description": (
                                "The file's complete new text. Required unless delete is true."
                            ),
                        },
                        "delete": {
                            "type": "boolean",
                            "description": "Delete this path instead of writing it.",
                        },
                    },
                    "required": ["path"],
                },
            },
            "github_token": {"type": "string", "description": "Optional token override."},
        },
        "required": ["owner", "repo", "base_branch", "head_branch", "title", "body", "changes"],
    },
    is_available=_pull_request_available,
    extract_params=_pull_request_extract_params,
    injected_params=GITHUB_CLI_INJECTED_PARAMS,
)
def open_github_pull_request(
    owner: str,
    repo: str,
    base_branch: str,
    head_branch: str,
    title: str,
    body: str,
    changes: Any = None,
    commit_message: str = "",
    draft: bool = False,
    github_token: str | None = None,
    **_kwargs: Any,
) -> dict[str, Any]:
    """Create a branch with *changes* and open a pull request for it."""
    token = resolve_github_token(github_token)
    if not token:
        return _failure(
            ERR_TOKEN,
            "No GitHub token is configured. Set GITHUB_MCP_AUTH_TOKEN, GITHUB_TOKEN, or GH_TOKEN.",
        )

    client = GitHubRestClient(token)
    branch_created = False
    try:
        parsed_changes = _parse_changes(changes)
        commit_sha = create_branch_with_changes(
            client,
            owner=owner,
            repo=repo,
            base_branch=base_branch,
            new_branch=head_branch,
            changes=parsed_changes,
            commit_message=commit_message.strip() or title,
        )
        branch_created = True
        pull_request = create_pull_request(
            client,
            owner=owner,
            repo=repo,
            head_branch=head_branch,
            base_branch=base_branch,
            title=title,
            body=body,
            draft=draft,
        )
    except GitHubWriteError as exc:
        message = exc.message
        if branch_created:
            # The commit landed and only the PR failed. Say so: the branch is on
            # the remote, so a naive retry hits "branch already exists" and the
            # real cause disappears.
            message = (
                f"{message} Branch '{head_branch}' was created and still holds the commit; "
                "open the pull request for it manually or retry with a new branch name."
            )
        return _failure(exc.kind, message, branch=head_branch, branch_created=branch_created)

    return {
        "ok": True,
        "error_kind": "",
        "error": "",
        "branch": head_branch,
        "branch_created": True,
        "base_branch": base_branch,
        "commit_sha": commit_sha,
        "files_changed": [change.path for change in parsed_changes],
        "pull_request_url": pull_request.url,
        "pull_request_number": pull_request.number,
    }
