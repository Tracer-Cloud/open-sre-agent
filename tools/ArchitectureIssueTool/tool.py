import ast
from pathlib import Path
from typing import Any

from tools.ArchitectureIssueTool.checkers import analyze_misplaced, is_compatibility_shim
from tools.ArchitectureIssueTool.utils import (
    _BASELINE_IGNORES,
    _FORBIDDEN_RULES,
    discover_packages,
    get_module_path,
    resolve_import,
)
from tools.tool_decorator import tool


@tool(
    name="find_architecture_violations",
    source="knowledge",
    description=(
        "Scan the codebase to detect architectural violations (dependency direction, "
        "oversized files, compatibility shims, misplaced modules) and propose atomic refactor tasks."
    ),
    surfaces=("investigation", "chat"),
    use_cases=[
        "Identify cyclic dependencies or forbidden cross-package imports.",
        "Find overly large files that need to be split.",
        "Locate compatibility-only forwarding modules that should be deleted.",
        "Detect tools misplaced in integration directories or vice versa.",
    ],
    input_schema={
        "type": "object",
        "properties": {
            "repo_root": {
                "type": "string",
                "description": "Optional absolute path to the repository root. If not provided, it will be automatically detected.",
            },
            "max_file_lines": {
                "type": "integer",
                "default": 500,
                "description": "Line count limit for identifying oversized files.",
            },
        },
    },
)
def find_architecture_violations(
    repo_root: str | None = None,
    max_file_lines: int = 500,
) -> dict[str, Any]:
    """Scan the codebase to detect architectural violations and propose refactor tasks."""
    if repo_root:
        root_path = Path(repo_root).resolve()
    else:
        current = Path(__file__).resolve()
        # Default to repo root assuming tools/ArchitectureIssueTool/tool.py
        root_path = current.parents[2] if len(current.parents) >= 3 else current.parent
        for parent in [current] + list(current.parents):
            if (parent / "pyproject.toml").exists() or (parent / ".git").exists():
                root_path = parent
                break

    packages = discover_packages(root_path)
    violations: list[dict[str, Any]] = []
    proposed_tasks: list[dict[str, Any]] = []

    # Find all Python files in first-party packages
    py_files: list[Path] = []
    for pkg in packages:
        pkg_path = root_path / pkg
        if not pkg_path.exists():
            continue
        for path in pkg_path.rglob("*.py"):
            if "__pycache__" in path.parts:
                continue
            if "tests" in path.parts or "fixtures" in path.parts:
                continue
            py_files.append(path)

    for file_path in py_files:
        try:
            content = file_path.read_text(encoding="utf-8")
        except Exception:
            continue

        rel_path_str = str(file_path.relative_to(root_path)).replace("\\", "/")
        source_pkg = file_path.relative_to(root_path).parts[0]
        module_path = get_module_path(file_path, root_path)
        is_init = file_path.name == "__init__.py"

        # 1. Dependency Direction Checker
        try:
            tree = ast.parse(content)
            file_imports: list[tuple[str, int]] = []
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    for alias in node.names:
                        top = alias.name.split(".", 1)[0]
                        if top in packages:
                            file_imports.append((alias.name, node.lineno))
                elif isinstance(node, ast.ImportFrom):
                    module_name = node.module or ""
                    resolved = resolve_import(module_path, module_name, node.level, is_init=is_init)
                    top = resolved.split(".", 1)[0]
                    if top in packages:
                        file_imports.append((resolved, node.lineno))

            for imported_module, lineno in file_imports:
                imported_pkg = imported_module.split(".", 1)[0]
                if source_pkg in _FORBIDDEN_RULES and imported_pkg in _FORBIDDEN_RULES[source_pkg]:
                    # Check if ignored by baseline
                    is_ignored = False
                    for ignore_edge in _BASELINE_IGNORES:
                        if "->" not in ignore_edge:
                            continue

                        ign_src, ign_dst = [p.strip() for p in ignore_edge.split("->", 1)]

                        # Match exactly or dot-prefix to avoid partial-word matches
                        src_match = (module_path == ign_src) or module_path.startswith(
                            f"{ign_src}."
                        )
                        dst_match = (imported_module == ign_dst) or imported_module.startswith(
                            f"{ign_dst}."
                        )

                        if src_match and dst_match:
                            is_ignored = True
                            break

                    violations.append(
                        {
                            "type": "dependency_direction",
                            "file_path": rel_path_str,
                            "description": (
                                f"Module '{module_path}' imports '{imported_module}', "
                                f"violating the '{source_pkg} -> {imported_pkg}' dependency restriction."
                                f"{' (Baseline Ignored)' if is_ignored else ''}"
                            ),
                            "details": {
                                "source_module": module_path,
                                "imported_module": imported_module,
                                "line_number": lineno,
                                "is_baseline_ignore": is_ignored,
                            },
                        }
                    )

                    if not is_ignored:
                        proposed_tasks.append(
                            {
                                "title": f"Fix dependency direction violation in {rel_path_str}",
                                "description": (
                                    f"Module '{module_path}' imports '{imported_module}', "
                                    f"which violates the dependency layering rules. Resolve this by moving "
                                    f"shared logic to a lower layer or using dependency injection/interfaces."
                                ),
                                "target_file": rel_path_str,
                                "priority": "high",
                                "difficulty": "medium",
                            }
                        )
        except Exception:
            # Ignore AST parsing errors for invalid Python files
            pass

        # 2. Oversized File Checker
        lines = content.splitlines()
        if len(lines) > max_file_lines:
            violations.append(
                {
                    "type": "oversized_file",
                    "file_path": rel_path_str,
                    "description": f"File exceeds the maximum line limit of {max_file_lines} lines (actual: {len(lines)}).",
                    "details": {
                        "line_count": len(lines),
                        "max_lines": max_file_lines,
                    },
                }
            )
            proposed_tasks.append(
                {
                    "title": f"Split oversized file {rel_path_str}",
                    "description": (
                        f"The file '{rel_path_str}' has {len(lines)} lines, which exceeds the limit "
                        f"of {max_file_lines}. Refactor by extracting logic into smaller modules "
                        f"with focused responsibilities."
                    ),
                    "target_file": rel_path_str,
                    "priority": "medium",
                    "difficulty": "medium",
                }
            )

        # 3. Compatibility Shim Checker
        if is_compatibility_shim(rel_path_str, content):
            violations.append(
                {
                    "type": "compatibility_shim",
                    "file_path": rel_path_str,
                    "description": "File is a compatibility-only forwarding module.",
                    "details": {},
                }
            )
            proposed_tasks.append(
                {
                    "title": f"Remove compatibility forwarding module {rel_path_str}",
                    "description": (
                        f"The module '{module_path}' is a compatibility-only forwarding module. "
                        f"Migrate all remaining import sites to use the canonical module directly, "
                        f"and delete this forwarding file."
                    ),
                    "target_file": rel_path_str,
                    "priority": "low",
                    "difficulty": "easy",
                }
            )

        # 4. Misplaced Module Checker
        is_misplaced, reason = analyze_misplaced(file_path, root_path, content)
        if is_misplaced:
            violations.append(
                {
                    "type": "misplaced_module",
                    "file_path": rel_path_str,
                    "description": reason,
                    "details": {},
                }
            )
            proposed_tasks.append(
                {
                    "title": f"Move misplaced module {rel_path_str}",
                    "description": (
                        f"The module '{module_path}' is misplaced. Reason: {reason} "
                        f"Relocate the module/files to align with the codebase conventions."
                    ),
                    "target_file": rel_path_str,
                    "priority": "high",
                    "difficulty": "medium",
                }
            )

    return {
        "violations": violations,
        "proposed_refactor_tasks": proposed_tasks,
    }
