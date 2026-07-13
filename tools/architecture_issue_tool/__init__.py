"""Architecture audit tools: clone, import/placement scans, and cleanup."""

from __future__ import annotations

from tools.architecture_issue_tool.models import (
    ArchitectureViolation,
    RefactorTask,
    ScanSummary,
    Severity,
    ViolationKind,
    build_error_result,
    build_success_result,
)
from tools.architecture_issue_tool.repo_workspace import (
    RepoWorkspace,
    WorkspaceError,
    architecture_workspace_dir,
    cleanup_architecture_workspace,
    clone_github_repo,
    cloned_github_repo,
    github_remote_url,
    resolve_scan_roots,
)
from tools.architecture_issue_tool.tool import (
    architecture_cleanup_repo,
    architecture_clone_repo,
    scan_architecture_imports,
    scan_module_placement_tool,
)

TOOL_MODULES = ("tool",)

__all__ = [
    "TOOL_MODULES",
    "ArchitectureViolation",
    "RefactorTask",
    "RepoWorkspace",
    "ScanSummary",
    "Severity",
    "ViolationKind",
    "WorkspaceError",
    "architecture_cleanup_repo",
    "architecture_clone_repo",
    "architecture_workspace_dir",
    "build_error_result",
    "build_success_result",
    "cleanup_architecture_workspace",
    "clone_github_repo",
    "cloned_github_repo",
    "github_remote_url",
    "resolve_scan_roots",
    "scan_architecture_imports",
    "scan_module_placement_tool",
]
