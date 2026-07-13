"""Action-surface architecture audit tools (clone and cleanup)."""

from __future__ import annotations

from typing import Any

from core.tool_framework.tool_decorator import tool
from integrations.github.client import resolve_github_token
from integrations.github.helpers import github_creds, github_source_available
from tools.architecture_issue_tool.repo_workspace import (
    WorkspaceError,
    architecture_workspace_dir,
    cleanup_architecture_workspace,
    clone_github_repo,
)


def _github_clone_available(sources: dict[str, dict]) -> bool:
    return bool(github_source_available(sources) or resolve_github_token(None))


def _github_extract_params(sources: dict[str, dict]) -> dict[str, Any]:
    gh = sources.get("github", {})
    if not gh:
        return {}
    payload: dict[str, Any] = {**github_creds(gh)}
    if gh.get("owner"):
        payload["owner"] = gh.get("owner")
    if gh.get("repo"):
        payload["repo"] = gh.get("repo")
    return payload


def _always_available(_sources: dict[str, dict]) -> bool:
    return True


@tool(
    name="architecture_clone_repo",
    source="github",
    description=(
        "Shallow-clone a GitHub repository into "
        ".temp/opensre/architecture_workspace for an architecture audit. "
        "Always call architecture_cleanup_repo when finished."
    ),
    use_cases=[
        "Preparing a local clone before architecture shell heuristic passes",
        "Architecture audit skill: clone then shell passes then cleanup",
    ],
    anti_examples=[
        "Leaving the clone on disk after the audit",
        "Cloning outside the architecture workspace",
    ],
    requires=["owner", "repo"],
    surfaces=("action",),
    side_effect_level="read_only",
    input_schema={
        "type": "object",
        "properties": {
            "owner": {"type": "string"},
            "repo": {"type": "string"},
            "ref": {"type": "string"},
            "github_token": {"type": "string"},
        },
        "required": ["owner", "repo"],
    },
    is_available=_github_clone_available,
    extract_params=_github_extract_params,
)
def architecture_clone_repo(
    owner: str,
    repo: str,
    ref: str = "",
    github_token: str | None = None,
    local_path: str | None = None,
    **_kwargs: Any,
) -> dict[str, Any]:
    """Clone owner/repo into the fixed architecture workspace."""
    try:
        workspace = clone_github_repo(
            owner,
            repo,
            ref=ref,
            token=github_token,
            local_path=local_path,
        )
    except WorkspaceError as exc:
        return {
            "ok": False,
            "owner": owner,
            "repo": repo,
            "ref": ref,
            "error": str(exc),
            "workspace_root": "",
        }
    return {
        "ok": True,
        "owner": workspace.owner,
        "repo": workspace.repo,
        "ref": workspace.ref,
        "workspace_root": str(workspace.root),
        "error": "",
    }


@tool(
    name="architecture_cleanup_repo",
    source="github",
    description=(
        "Delete .temp/opensre/architecture_workspace after an architecture audit. "
        "Refuses paths outside that directory."
    ),
    use_cases=["Cleanup after architecture_clone_repo"],
    anti_examples=["Deleting arbitrary paths outside the architecture workspace"],
    surfaces=("action",),
    side_effect_level="read_only",
    input_schema={
        "type": "object",
        "properties": {
            "workspace_root": {
                "type": "string",
                "description": "Optional path; must be under the architecture workspace.",
            }
        },
        "required": [],
    },
    is_available=_always_available,
)
def architecture_cleanup_repo(
    workspace_root: str = "",
    **_kwargs: Any,
) -> dict[str, Any]:
    """Remove the architecture clone workspace."""
    try:
        removed = cleanup_architecture_workspace(
            path=workspace_root or architecture_workspace_dir()
        )
    except WorkspaceError as exc:
        return {"ok": False, "removed_path": "", "error": str(exc)}
    return {"ok": True, "removed_path": str(removed), "error": ""}
