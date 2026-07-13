"""Tool and integration placement heuristics."""

from __future__ import annotations

import ast
import hashlib
import re
from pathlib import Path

from tools.architecture_issue_tool.models import ArchitectureViolation
from tools.architecture_issue_tool.scanners._paths import iter_py_files, rel_path

_KNOWN_VENDOR_TOOL_PACKAGES = frozenset(
    {
        "community_followup_tool",
        "git_deploy_timeline_tool",
        "work_status_report_tool",
        "slack_send_message_tool",
        "pi_coding_tool",
    }
)
_LEGACY_IMPORT_RE = re.compile(r"^\s*(?:from|import)\s+(vendors|services)(?:\.|\s)")
_FAT_INIT_MIN_LINES = 80
_FAT_INIT_MIN_FUNCTIONS = 2


def _violation_id(path: str, pattern: str) -> str:
    digest = hashlib.sha256(f"placement:{path}:{pattern}".encode()).hexdigest()
    return f"m-{digest[:12]}"


def _integration_vendors(tree: ast.Module) -> set[str]:
    vendors: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            if node.module.startswith("integrations."):
                parts = node.module.split(".")
                if len(parts) >= 2:
                    vendors.add(parts[1])
        elif isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name.startswith("integrations."):
                    parts = alias.name.split(".")
                    if len(parts) >= 2:
                        vendors.add(parts[1])
    return vendors


def _imports_tools(tree: ast.Module) -> bool:
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module and node.module.startswith("tools."):
            return True
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name == "tools" or alias.name.startswith("tools."):
                    return True
    return False


def _scan_known_vendor_tools(clone_root: Path) -> list[ArchitectureViolation]:
    tools_dir = clone_root / "tools"
    if not tools_dir.is_dir():
        return []

    violations: list[ArchitectureViolation] = []
    for package_name in sorted(_KNOWN_VENDOR_TOOL_PACKAGES):
        package_path = tools_dir / package_name
        if not package_path.is_dir():
            continue
        repo_path = rel_path(clone_root, package_path)
        violations.append(
            ArchitectureViolation(
                id=_violation_id(repo_path, "known_vendor_tool"),
                kind="misplaced_module",
                severity="p2",
                title=f"Vendor tool still under tools/: {package_name}",
                evidence={
                    "path": repo_path,
                    "pattern": "known_vendor_tool",
                    "suggested_location": "integrations/<vendor>/tools/",
                },
                fix_direction=(
                    f"Move {package_name} under integrations/<vendor>/tools/ "
                    "(or the repo's equivalent vendor-owned tools package)."
                ),
            )
        )
    return violations


def _scan_single_vendor_tools(clone_root: Path) -> list[ArchitectureViolation]:
    tools_dir = clone_root / "tools"
    if not tools_dir.is_dir():
        return []

    violations: list[ArchitectureViolation] = []
    for package_path in sorted(tools_dir.iterdir()):
        if not package_path.is_dir() or package_path.name.startswith("."):
            continue
        if package_path.name in _KNOWN_VENDOR_TOOL_PACKAGES:
            continue

        init_path = package_path / "__init__.py"
        if not init_path.is_file():
            continue

        try:
            tree = ast.parse(init_path.read_text(encoding="utf-8"), filename=str(init_path))
        except SyntaxError:
            continue

        vendors = _integration_vendors(tree)
        if len(vendors) != 1:
            continue

        vendor = next(iter(vendors))
        repo_path = rel_path(clone_root, package_path)
        violations.append(
            ArchitectureViolation(
                id=_violation_id(repo_path, f"single_vendor:{vendor}"),
                kind="misplaced_module",
                severity="p2",
                title=f"Single-vendor tool candidate: {package_path.name}",
                evidence={
                    "path": repo_path,
                    "pattern": "single_vendor_tool",
                    "vendor": vendor,
                    "suggested_location": f"integrations/{vendor}/tools/",
                },
                fix_direction=(
                    f"Consider moving {package_path.name} to integrations/{vendor}/tools/ "
                    "because it only imports one vendor integration."
                ),
            )
        )
    return violations


def _scan_fat_tool_inits(clone_root: Path) -> list[ArchitectureViolation]:
    tools_dir = clone_root / "tools"
    if not tools_dir.is_dir():
        return []

    violations: list[ArchitectureViolation] = []
    for package_path in sorted(tools_dir.iterdir()):
        init_path = package_path / "__init__.py"
        if not init_path.is_file():
            continue

        source = init_path.read_text(encoding="utf-8", errors="replace")
        line_count = len([line for line in source.splitlines() if line.strip()])
        if line_count <= _FAT_INIT_MIN_LINES:
            continue

        try:
            tree = ast.parse(source, filename=str(init_path))
        except SyntaxError:
            continue

        function_count = sum(
            1 for node in tree.body if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef)
        )
        if function_count < _FAT_INIT_MIN_FUNCTIONS:
            continue

        repo_path = rel_path(clone_root, init_path)
        violations.append(
            ArchitectureViolation(
                id=_violation_id(repo_path, "fat_init"),
                kind="misplaced_module",
                severity="p2",
                title=f"Non-trivial logic in tool __init__.py: {repo_path}",
                evidence={
                    "path": repo_path,
                    "pattern": "fat_init",
                    "line_count": line_count,
                    "function_count": function_count,
                },
                fix_direction=(
                    f"Split helpers out of {repo_path} into focused sibling modules "
                    "instead of hiding tool logic entirely in __init__.py."
                ),
            )
        )
    return violations


def _scan_integrations_import_tools(clone_root: Path) -> list[ArchitectureViolation]:
    integrations_dir = clone_root / "integrations"
    if not integrations_dir.is_dir():
        return []

    violations: list[ArchitectureViolation] = []
    for path in iter_py_files(clone_root, [integrations_dir]):
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        except SyntaxError:
            continue
        if not _imports_tools(tree):
            continue

        repo_path = rel_path(clone_root, path)
        violations.append(
            ArchitectureViolation(
                id=_violation_id(repo_path, "integrations_import_tools"),
                kind="misplaced_module",
                severity="p2",
                title=f"integrations imports tools: {repo_path}",
                evidence={
                    "path": repo_path,
                    "pattern": "integrations_import_tools",
                },
                fix_direction=(
                    "Move shared code below integrations/ or refactor so integrations "
                    "does not import from tools/."
                ),
            )
        )
    return violations


def _scan_legacy_top_level_imports(clone_root: Path) -> list[ArchitectureViolation]:
    scan_roots = [path for path in clone_root.iterdir() if path.is_dir()]
    violations: list[ArchitectureViolation] = []

    for path in iter_py_files(clone_root, scan_roots):
        for line_no, line in enumerate(
            path.read_text(encoding="utf-8", errors="replace").splitlines(),
            start=1,
        ):
            match = _LEGACY_IMPORT_RE.match(line)
            if not match:
                continue
            legacy_pkg = match.group(1)
            repo_path = rel_path(clone_root, path)
            violations.append(
                ArchitectureViolation(
                    id=_violation_id(repo_path, f"legacy:{legacy_pkg}:{line_no}"),
                    kind="misplaced_module",
                    severity="p0",
                    title=f"Legacy top-level import: {legacy_pkg}",
                    evidence={
                        "path": repo_path,
                        "pattern": "legacy_top_level_package",
                        "package": legacy_pkg,
                        "line": line_no,
                    },
                    fix_direction=(
                        f"Remove imports from banned top-level package {legacy_pkg}/ "
                        "and use the repo's integration/vendor package layout instead."
                    ),
                )
            )
    return violations


def scan_module_placement(clone_root: Path) -> list[ArchitectureViolation]:
    """Detect likely misplaced modules and banned legacy import paths."""
    violations: list[ArchitectureViolation] = []
    violations.extend(_scan_known_vendor_tools(clone_root))
    violations.extend(_scan_single_vendor_tools(clone_root))
    violations.extend(_scan_fat_tool_inits(clone_root))
    violations.extend(_scan_integrations_import_tools(clone_root))
    violations.extend(_scan_legacy_top_level_imports(clone_root))
    return violations
