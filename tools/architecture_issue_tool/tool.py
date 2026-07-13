"""Action-surface architecture audit tools (clone, import, placement, cleanup)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from core.tool_framework.tool_decorator import tool
from integrations.github.client import resolve_github_token
from integrations.github.helpers import github_creds, github_source_available
from tools.architecture_issue_tool.models import build_error_result
from tools.architecture_issue_tool.repo_workspace import (
    WorkspaceError,
    architecture_workspace_dir,
    cleanup_architecture_workspace,
    clone_github_repo,
)
from tools.architecture_issue_tool.scan import scan_imports_at_path, scan_placement_at_path


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


def _resolve_workspace_root(workspace_root: str) -> Path:
    root = Path(workspace_root).expanduser().resolve()
    if not root.is_dir():
        raise WorkspaceError(f"workspace_root is not a directory: {root}")
    return root


@tool(
    name="architecture_clone_repo",
    source="github",
    description=(
        "Shallow-clone a GitHub repository into "
        ".temp/opensre/architecture_workspace for an architecture audit. "
        "Always call architecture_cleanup_repo when finished."
    ),
    use_cases=[
        "Preparing a local clone before architecture import/placement scans",
        "Architecture audit skill: clone then scan then cleanup",
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
    name="scan_architecture_imports",
    source="github",
    description=(
        "Scan a local workspace_root for layer_import and direct_import violations "
        "using the polyglot import graph. Pass the path from architecture_clone_repo."
    ),
    use_cases=["Architecture audit import checks after cloning a repo"],
    anti_examples=["Scanning without a workspace_root from architecture_clone_repo"],
    requires=["workspace_root"],
    surfaces=("action",),
    side_effect_level="read_only",
    input_schema={
        "type": "object",
        "properties": {
            "workspace_root": {"type": "string"},
            "owner": {"type": "string"},
            "repo": {"type": "string"},
            "ref": {"type": "string"},
            "strict_layers": {"type": "boolean", "default": True},
            "include_baselines": {"type": "boolean", "default": False},
        },
        "required": ["workspace_root"],
    },
    is_available=_always_available,
)
def scan_architecture_imports(
    workspace_root: str,
    owner: str = "",
    repo: str = "",
    ref: str = "",
    strict_layers: bool = True,
    include_baselines: bool = False,
    **_kwargs: Any,
) -> dict[str, Any]:
    """Run import scanners against workspace_root."""
    try:
        root = _resolve_workspace_root(workspace_root)
        return scan_imports_at_path(
            root,
            owner=owner,
            repo=repo,
            ref=ref,
            strict_layers=strict_layers,
            include_baselines=include_baselines,
        )
    except WorkspaceError as exc:
        return build_error_result(owner=owner, repo=repo, error=str(exc), ref=ref)


@tool(
    name="scan_module_placement",
    source="github",
    description=(
        "Scan a local workspace_root for misplaced_module violations "
        "(vendor/tool placement, fat __init__.py, banned legacy top-level "
        "imports). Applies when matching layout patterns exist; otherwise "
        "returns zero findings. Pass the path from architecture_clone_repo."
    ),
    use_cases=["Architecture audit placement checks after cloning a repo"],
    anti_examples=["Using this for oversized-file or shim detection"],
    requires=["workspace_root"],
    surfaces=("action",),
    side_effect_level="read_only",
    input_schema={
        "type": "object",
        "properties": {
            "workspace_root": {"type": "string"},
            "owner": {"type": "string"},
            "repo": {"type": "string"},
            "ref": {"type": "string"},
        },
        "required": ["workspace_root"],
    },
    is_available=_always_available,
)
def scan_module_placement_tool(
    workspace_root: str,
    owner: str = "",
    repo: str = "",
    ref: str = "",
    **_kwargs: Any,
) -> dict[str, Any]:
    """Run module-placement scanners against workspace_root."""
    try:
        root = _resolve_workspace_root(workspace_root)
        return scan_placement_at_path(root, owner=owner, repo=repo, ref=ref)
    except WorkspaceError as exc:
        return build_error_result(owner=owner, repo=repo, error=str(exc), ref=ref)


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
