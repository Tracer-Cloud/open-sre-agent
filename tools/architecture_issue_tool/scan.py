"""Orchestrate architecture scans across cloned repository workspaces."""

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
from tools.architecture_issue_tool.repo_workspace import RepoWorkspace
from tools.architecture_issue_tool.scanners._paths import iter_py_files
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

_IMPORT_SKIP_MARKERS = (
    "no supported source files found",
    "import graph build failed",
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


def _has_python_files(clone_root: Path) -> bool:
    return any(iter_py_files(clone_root, [clone_root]))


def _has_opensre_layout(clone_root: Path) -> bool:
    return (clone_root / "tools").is_dir() or (clone_root / "integrations").is_dir()


def _import_categories_skipped(
    selected: list[ViolationKind],
    import_warnings: list[str],
) -> list[ViolationKind]:
    skipped: list[ViolationKind] = []
    if not any(marker in warning for marker in _IMPORT_SKIP_MARKERS for warning in import_warnings):
        return skipped
    if "layer_import" in selected:
        skipped.append("layer_import")
    if "direct_import" in selected:
        skipped.append("direct_import")
    return skipped


def _python_only_categories_skipped(
    clone_root: Path,
    selected: list[ViolationKind],
    warnings: list[str],
) -> list[ViolationKind]:
    skipped: list[ViolationKind] = []
    has_python = _has_python_files(clone_root)
    if "oversized_file" in selected and not has_python:
        skipped.append("oversized_file")
        warnings.append("oversized_file checks skipped: no Python files found")
    if "compatibility_shim" in selected and not has_python:
        skipped.append("compatibility_shim")
        warnings.append("compatibility_shim checks skipped: no Python files found")
    if "misplaced_module" in selected and not _has_opensre_layout(clone_root):
        skipped.append("misplaced_module")
        warnings.append("misplaced_module checks skipped: OpenSRE layout markers not found")
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
        hotspots=build_hotspots(violations),
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

    python_only_skipped = _python_only_categories_skipped(clone_root, selected, warnings)

    if "oversized_file" in selected and "oversized_file" not in python_only_skipped:
        violations.extend(scan_oversized_files(clone_root, max_lines=max_lines))

    if "compatibility_shim" in selected and "compatibility_shim" not in python_only_skipped:
        violations.extend(scan_compatibility_shims(clone_root))

    if "misplaced_module" in selected and "misplaced_module" not in python_only_skipped:
        violations.extend(scan_module_placement(clone_root))

    deduped = dedupe_violations(violations)
    tasks = build_refactor_tasks(deduped)
    categories_skipped = _import_categories_skipped(selected, import_warnings)
    categories_skipped.extend(python_only_skipped)
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
