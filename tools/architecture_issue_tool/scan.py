"""Orchestrate architecture scans across cloned repository workspaces."""

from __future__ import annotations

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


def _selected_categories(categories: list[ViolationKind] | None) -> list[ViolationKind]:
    if categories:
        return list(categories)
    return list(_DEFAULT_CATEGORIES)


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
    summary = ScanSummary(
        violations=len(deduped),
        tasks=len(tasks),
        warnings=warnings,
        categories_scanned=selected,
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
