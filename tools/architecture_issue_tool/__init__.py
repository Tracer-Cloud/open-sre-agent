"""Architecture audit tool: scan GitHub repos for violations and propose refactor tasks."""

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
    cloned_github_repo,
    github_remote_url,
    resolve_scan_roots,
)
from tools.architecture_issue_tool.tool import find_architecture_violations

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
    "build_error_result",
    "build_success_result",
    "cloned_github_repo",
    "find_architecture_violations",
    "github_remote_url",
    "resolve_scan_roots",
]
