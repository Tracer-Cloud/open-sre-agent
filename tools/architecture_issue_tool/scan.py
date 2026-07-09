"""Orchestrate architecture scans across cloned repository workspaces."""

from __future__ import annotations

from collections import Counter
from pathlib import Path
from typing import Any

from tools.architecture_issue_tool.models import (
    ArchitectureViolation,
    ScanSummary,
    ViolationKind,
    build_success_result,
)
from tools.architecture_issue_tool.refactor_tasks import build_refactor_tasks, dedupe_violations
from tools.architecture_issue_tool.repo_workspace import RepoWorkspace
from tools.architecture_issue_tool.scanners.compatibility_shims import scan_compatibility_shims
from tools.architecture_issue_tool.scanners.import_checks import scan_import_violations
from tools.architecture_issue_tool.scanners.module_placement import scan_module_placement
from tools.architecture_issue_tool.scanners.oversized_files import scan_oversized_files

_DEFAULT_CATEGORIES: tuple[ViolationKind, ...] = (
    "layer_import",
    "direct_import",
    "oversized_file",
    "compatibility_shim",
    "misplaced_module",
)

_LAYER_SKIP_MARKERS = (
    "no import-linter config found",
    "lint-imports is not installed",
)


def _selected_categories(categories: list[ViolationKind] | None) -> list[ViolationKind]:
    if categories:
        return list(categories)
    return list(_DEFAULT_CATEGORIES)


def _severity_counts(violations: list[ArchitectureViolation]) -> dict[str, int]:
    counts: dict[str, int] = dict.fromkeys(("p0", "p1", "p2"), 0)
    for violation in violations:
        counts[violation.severity] = counts.get(violation.severity, 0) + 1
    return counts


def _kind_counts(violations: list[ArchitectureViolation]) -> dict[str, int]:
    return dict(Counter(violation.kind for violation in violations))


def _import_categories_skipped(
    clone_root: Path,
    selected: list[ViolationKind],
    import_warnings: list[str],
) -> list[ViolationKind]:
    skipped: list[ViolationKind] = []
    if "layer_import" in selected and any(
        any(marker in warning for marker in _LAYER_SKIP_MARKERS) for warning in import_warnings
    ):
        skipped.append("layer_import")
    if "direct_import" in selected:
        script = clone_root / ".github" / "ci" / "check_direct_imports.py"
        if not script.is_file():
            skipped.append("direct_import")
    return skipped


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
        coverage_complete=coverage_complete,
    )


def run_architecture_scan(
    workspace: RepoWorkspace,
    *,
    max_lines: int = 500,
    strict_layers: bool = True,
    include_baselines: bool = False,
    categories: list[ViolationKind] | None = None,
) -> dict[str, Any]:
    """Run selected scanners against *workspace* and return the tool payload."""
    selected = _selected_categories(categories)
    clone_root = workspace.root
    warnings: list[str] = []
    violations: list[ArchitectureViolation] = []
    import_warnings: list[str] = []

    if "layer_import" in selected or "direct_import" in selected:
        import_violations, import_warnings = scan_import_violations(
            clone_root,
            strict_layers=strict_layers,
            include_baselines=include_baselines,
        )
        if "layer_import" in selected:
            violations.extend(v for v in import_violations if v.kind == "layer_import")
        if "direct_import" in selected:
            violations.extend(v for v in import_violations if v.kind == "direct_import")
        warnings.extend(import_warnings)

    if "oversized_file" in selected:
        violations.extend(scan_oversized_files(clone_root, max_lines=max_lines))

    if "compatibility_shim" in selected:
        violations.extend(scan_compatibility_shims(clone_root))

    if "misplaced_module" in selected:
        violations.extend(scan_module_placement(clone_root))

    deduped = dedupe_violations(violations)
    tasks = build_refactor_tasks(deduped)
    categories_skipped = _import_categories_skipped(clone_root, selected, import_warnings)
    summary = _build_scan_summary(
        violations=deduped,
        tasks=len(tasks),
        warnings=warnings,
        categories_scanned=selected,
        categories_skipped=categories_skipped,
    )

    return build_success_result(
        owner=workspace.owner,
        repo=workspace.repo,
        ref=workspace.ref,
        violations=deduped,
        refactor_tasks=tasks,
        scan_summary=summary,
        workspace_root=str(clone_root),
    )
