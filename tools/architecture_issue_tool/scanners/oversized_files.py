"""Oversized Python source file scanner."""

from __future__ import annotations

import hashlib
from pathlib import Path

from tools.architecture_issue_tool.models import ArchitectureViolation
from tools.architecture_issue_tool.repo_workspace import resolve_scan_roots
from tools.architecture_issue_tool.scanners._paths import iter_py_files, rel_path

_MIN_MAX_LINES = 100


def _effective_max_lines(max_lines: int) -> int:
    return max(_MIN_MAX_LINES, max_lines)


def _count_code_lines(path: Path) -> int:
    count = 0
    for raw_line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        stripped = raw_line.strip()
        if not stripped:
            continue
        if stripped.startswith("#"):
            continue
        code = stripped.split("#", 1)[0].strip()
        if code:
            count += 1
    return count


def _violation_id(path: str, line_count: int) -> str:
    digest = hashlib.sha256(f"oversized:{path}:{line_count}".encode()).hexdigest()
    return f"o-{digest[:12]}"


def scan_oversized_files(
    clone_root: Path,
    *,
    max_lines: int = 500,
) -> list[ArchitectureViolation]:
    """Flag Python files that exceed the configured line-count threshold."""
    threshold = _effective_max_lines(max_lines)
    scan_roots = resolve_scan_roots(clone_root)
    violations: list[ArchitectureViolation] = []

    for path in iter_py_files(clone_root, scan_roots):
        line_count = _count_code_lines(path)
        if line_count <= threshold:
            continue

        repo_path = rel_path(clone_root, path)
        violations.append(
            ArchitectureViolation(
                id=_violation_id(repo_path, line_count),
                kind="oversized_file",
                severity="p2",
                title=f"Oversized file: {repo_path}",
                evidence={
                    "path": repo_path,
                    "line_count": line_count,
                    "threshold": threshold,
                },
                fix_direction=(
                    f"Extract helpers from {repo_path} into focused sibling modules "
                    f"until the file is at or below {threshold} non-blank code lines."
                ),
            )
        )

    return violations
