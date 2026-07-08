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

__all__ = [
    "ArchitectureViolation",
    "RefactorTask",
    "ScanSummary",
    "Severity",
    "ViolationKind",
    "build_error_result",
    "build_success_result",
]
