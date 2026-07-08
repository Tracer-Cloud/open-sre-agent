"""Compatibility shim and re-export scanner."""

from __future__ import annotations

import ast
import hashlib
from pathlib import Path

from tools.architecture_issue_tool.models import ArchitectureViolation
from tools.architecture_issue_tool.repo_workspace import resolve_scan_roots
from tools.architecture_issue_tool.scanners._paths import iter_py_files, rel_path

_SHIM_KEYWORDS = (
    "re-export",
    "compat shim",
    "forwarding module",
    "backward compat",
)


def _violation_id(path: str, pattern: str) -> str:
    digest = hashlib.sha256(f"shim:{path}:{pattern}".encode()).hexdigest()
    return f"s-{digest[:12]}"


def _has_local_definitions(tree: ast.Module) -> bool:
    for node in tree.body:
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef):
            return True
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id != "__all__":
                    return True
    return False


def _is_reexport_only_init(path: Path, tree: ast.Module) -> bool:
    if path.name != "__init__.py":
        return False
    if _has_local_definitions(tree):
        return False
    return any(isinstance(node, ast.Import | ast.ImportFrom) for node in tree.body)


def _keyword_hits(source: str) -> list[str]:
    lowered = source.lower()
    return [keyword for keyword in _SHIM_KEYWORDS if keyword in lowered]


def _pkg_import_sub_violation(
    clone_root: Path,
    path: Path,
    tree: ast.Module,
) -> ArchitectureViolation | None:
    repo_path = rel_path(clone_root, path)
    top_package = repo_path.split("/", 1)[0] if "/" in repo_path else ""

    for node in tree.body:
        if not isinstance(node, ast.ImportFrom) or node.level != 0 or not node.module:
            continue
        if not node.module.startswith(f"{top_package}."):
            continue
        imported = node.module.split(".", 1)[0]
        if imported != top_package:
            continue
        if len(node.module.split(".")) < 2:
            continue
        return ArchitectureViolation(
            id=_violation_id(repo_path, "from_pkg_import_sub"),
            kind="compatibility_shim",
            severity="p1",
            title=f"Compatibility import pattern in {repo_path}",
            evidence={
                "path": repo_path,
                "pattern": "from_pkg_import_sub",
                "module": node.module,
                "line": node.lineno,
            },
            fix_direction=(
                f"Replace `from {top_package} import <submodule>` with a direct submodule "
                f"import in {repo_path} to avoid package re-export loops."
            ),
        )
    return None


def scan_compatibility_shims(clone_root: Path) -> list[ArchitectureViolation]:
    """Detect likely compatibility-only forwarding modules."""
    scan_roots = resolve_scan_roots(clone_root)
    violations: list[ArchitectureViolation] = []

    for path in iter_py_files(clone_root, scan_roots):
        source = path.read_text(encoding="utf-8", errors="replace")
        try:
            tree = ast.parse(source, filename=str(path))
        except SyntaxError:
            continue

        repo_path = rel_path(clone_root, path)

        if _is_reexport_only_init(path, tree):
            violations.append(
                ArchitectureViolation(
                    id=_violation_id(repo_path, "reexport_only_init"),
                    kind="compatibility_shim",
                    severity="p1",
                    title=f"Re-export-only module: {repo_path}",
                    evidence={
                        "path": repo_path,
                        "pattern": "reexport_only_init",
                    },
                    fix_direction=(
                        f"Migrate callers to canonical import paths and delete the "
                        f"forwarding module {repo_path}."
                    ),
                )
            )

        for keyword in _keyword_hits(source):
            violations.append(
                ArchitectureViolation(
                    id=_violation_id(repo_path, keyword),
                    kind="compatibility_shim",
                    severity="p1",
                    title=f"Compatibility shim marker in {repo_path}",
                    evidence={
                        "path": repo_path,
                        "pattern": keyword,
                    },
                    fix_direction=(
                        f"Remove the compatibility shim at {repo_path} after migrating "
                        f"callers to the canonical module path."
                    ),
                )
            )

        pkg_violation = _pkg_import_sub_violation(clone_root, path, tree)
        if pkg_violation is not None:
            violations.append(pkg_violation)

    return violations
