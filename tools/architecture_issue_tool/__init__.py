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

__all__ = [
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
    "github_remote_url",
    "resolve_scan_roots",
]
