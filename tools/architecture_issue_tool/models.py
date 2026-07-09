"""Data models and stable return shapes for architecture violation scans."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

ViolationKind = Literal[
    "layer_import",
    "direct_import",
    "oversized_file",
    "compatibility_shim",
    "misplaced_module",
]

Severity = Literal["p0", "p1", "p2"]

DEFAULT_REFACTOR_LABELS: tuple[str, ...] = ("refactor", "maintainability", "agent-ready")


@dataclass(frozen=True)
class ArchitectureViolation:
    """One architecture finding with evidence and remediation guidance."""

    id: str
    kind: ViolationKind
    severity: Severity
    title: str
    evidence: dict[str, Any]
    fix_direction: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "kind": self.kind,
            "severity": self.severity,
            "title": self.title,
            "evidence": dict(self.evidence),
            "fix_direction": self.fix_direction,
        }


@dataclass(frozen=True)
class RefactorTask:
    """Atomic refactor suggestion derived from one or more violations."""

    task_id: str
    title: str
    description: str
    scope_files: list[str]
    acceptance_criteria: list[str]
    labels: list[str]
    related_violation_ids: list[str]
    suggested_issue_body: str = ""

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "task_id": self.task_id,
            "title": self.title,
            "description": self.description,
            "scope_files": list(self.scope_files),
            "acceptance_criteria": list(self.acceptance_criteria),
            "labels": list(self.labels),
            "related_violation_ids": list(self.related_violation_ids),
        }
        if self.suggested_issue_body:
            payload["suggested_issue_body"] = self.suggested_issue_body
        return payload


@dataclass(frozen=True)
class ScanSummary:
    """High-level scan metadata and counts."""

    violations: int = 0
    tasks: int = 0
    warnings: list[str] = field(default_factory=list)
    categories_scanned: list[ViolationKind] = field(default_factory=list)
    categories_skipped: list[ViolationKind] = field(default_factory=list)
    severity_counts: dict[str, int] = field(default_factory=dict)
    kind_counts: dict[str, int] = field(default_factory=dict)
    coverage_complete: bool = True

    def to_dict(self) -> dict[str, Any]:
        return {
            "violations": self.violations,
            "tasks": self.tasks,
            "warnings": list(self.warnings),
            "categories_scanned": list(self.categories_scanned),
            "categories_skipped": list(self.categories_skipped),
            "severity_counts": dict(self.severity_counts),
            "kind_counts": dict(self.kind_counts),
            "coverage_complete": self.coverage_complete,
        }


def build_success_result(
    *,
    owner: str,
    repo: str,
    ref: str,
    violations: list[ArchitectureViolation],
    refactor_tasks: list[RefactorTask],
    scan_summary: ScanSummary | None = None,
    workspace_root: str = "",
) -> dict[str, Any]:
    """Build the stable success payload for ``find_architecture_violations``."""

    summary = scan_summary or ScanSummary(
        violations=len(violations),
        tasks=len(refactor_tasks),
    )
    if summary.violations != len(violations) or summary.tasks != len(refactor_tasks):
        summary = ScanSummary(
            violations=len(violations),
            tasks=len(refactor_tasks),
            warnings=list(summary.warnings),
            categories_scanned=list(summary.categories_scanned),
            categories_skipped=list(summary.categories_skipped),
            severity_counts=dict(summary.severity_counts),
            kind_counts=dict(summary.kind_counts),
            coverage_complete=summary.coverage_complete,
        )

    payload: dict[str, Any] = {
        "source": "github",
        "available": True,
        "owner": owner,
        "repo": repo,
        "ref": ref,
        "scan_summary": summary.to_dict(),
        "violations": [item.to_dict() for item in violations],
        "refactor_tasks": [item.to_dict() for item in refactor_tasks],
        "side_effects": [],
    }
    if workspace_root:
        payload["workspace_root"] = workspace_root
    return payload


def build_error_result(
    *,
    owner: str,
    repo: str,
    error: str,
    ref: str = "",
    warnings: list[str] | None = None,
) -> dict[str, Any]:
    """Build the stable failure payload for ``find_architecture_violations``."""

    return {
        "source": "github",
        "available": False,
        "owner": owner,
        "repo": repo,
        "ref": ref,
        "error": error,
        "scan_summary": ScanSummary(
            warnings=list(warnings or []),
            coverage_complete=False,
        ).to_dict(),
        "violations": [],
        "refactor_tasks": [],
        "side_effects": [],
    }
