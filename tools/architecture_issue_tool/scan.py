"""Scan helpers for architecture import and placement tools."""

from __future__ import annotations

from collections import Counter
from pathlib import Path
from typing import Any

from tools.architecture_issue_tool.hotspots import build_hotspots
from tools.architecture_issue_tool.models import (
    ArchitectureViolation,
    ScanSummary,
    ViolationKind,
    build_success_result,
)
from tools.architecture_issue_tool.refactor_tasks import build_refactor_tasks, dedupe_violations
from tools.architecture_issue_tool.scanners.import_checks import scan_import_violations
from tools.architecture_issue_tool.scanners.module_placement import scan_module_placement

_IMPORT_SKIP_MARKERS = (
    "no supported source files found",
    "import graph build failed",
)


def _severity_counts(violations: list[ArchitectureViolation]) -> dict[str, int]:
    counts: dict[str, int] = dict.fromkeys(("p0", "p1", "p2"), 0)
    for violation in violations:
        counts[violation.severity] = counts.get(violation.severity, 0) + 1
    return counts


def _kind_counts(violations: list[ArchitectureViolation]) -> dict[str, int]:
    return dict(Counter(violation.kind for violation in violations))


def _build_scan_summary(
    *,
    violations: list[ArchitectureViolation],
    tasks: int,
    warnings: list[str],
    categories_scanned: list[ViolationKind],
    categories_skipped: list[ViolationKind],
) -> ScanSummary:
    coverage_complete = not warnings and not categories_skipped
    return ScanSummary(
        violations=len(violations),
        tasks=tasks,
        warnings=warnings,
        categories_scanned=categories_scanned,
        categories_skipped=categories_skipped,
        severity_counts=_severity_counts(violations),
        kind_counts=_kind_counts(violations),
        hotspots=build_hotspots(violations),
        coverage_complete=coverage_complete,
    )


def _result_payload(
    *,
    owner: str,
    repo: str,
    ref: str,
    workspace_root: Path,
    violations: list[ArchitectureViolation],
    warnings: list[str],
    categories_scanned: list[ViolationKind],
    categories_skipped: list[ViolationKind],
) -> dict[str, Any]:
    deduped = dedupe_violations(violations)
    tasks = build_refactor_tasks(deduped)
    summary = _build_scan_summary(
        violations=deduped,
        tasks=len(tasks),
        warnings=warnings,
        categories_scanned=categories_scanned,
        categories_skipped=categories_skipped,
    )
    return build_success_result(
        owner=owner,
        repo=repo,
        ref=ref,
        violations=deduped,
        refactor_tasks=tasks,
        scan_summary=summary,
        workspace_root=str(workspace_root),
    )


def scan_imports_at_path(
    workspace_root: Path,
    *,
    owner: str = "",
    repo: str = "",
    ref: str = "",
    strict_layers: bool = True,
    include_baselines: bool = False,
) -> dict[str, Any]:
    """Run layer/direct import scanners against *workspace_root*.

    Layer contracts are inferred from the repo layout (or a generic monorepo
    profile) — not from an OpenSRE-specific package map.
    """
    warnings: list[str] = []
    categories_scanned: list[ViolationKind] = ["layer_import", "direct_import"]
    categories_skipped: list[ViolationKind] = []

    import_violations, import_warnings = scan_import_violations(
        workspace_root,
        strict_layers=strict_layers,
        include_baselines=include_baselines,
    )
    warnings.extend(import_warnings)
    if any(marker in warning for marker in _IMPORT_SKIP_MARKERS for warning in import_warnings):
        categories_skipped = list(categories_scanned)

    return _result_payload(
        owner=owner,
        repo=repo,
        ref=ref,
        workspace_root=workspace_root,
        violations=list(import_violations),
        warnings=warnings,
        categories_scanned=categories_scanned,
        categories_skipped=categories_skipped,
    )


def scan_placement_at_path(
    workspace_root: Path,
    *,
    owner: str = "",
    repo: str = "",
    ref: str = "",
) -> dict[str, Any]:
    """Run module-placement scanners against *workspace_root*.

    Heuristics are layout-agnostic: checks that need ``tools/`` or
    ``integrations/`` simply yield no findings when those dirs are absent,
    instead of marking coverage incomplete.
    """
    categories_scanned: list[ViolationKind] = ["misplaced_module"]
    violations = scan_module_placement(workspace_root)

    return _result_payload(
        owner=owner,
        repo=repo,
        ref=ref,
        workspace_root=workspace_root,
        violations=violations,
        warnings=[],
        categories_scanned=categories_scanned,
        categories_skipped=[],
    )
