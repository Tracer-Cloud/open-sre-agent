"""Tool for detecting architecture issues and proposing refactoring tasks."""

from __future__ import annotations

import ast
from pathlib import Path
from typing import Any

from tools.tool_decorator import tool

_SKIP_ROOT_DIRS = frozenset(
    {
        ".git",
        ".github",
        ".pytest_cache",
        ".ruff_cache",
        ".venv",
        "__pycache__",
        "docs",
        "infra",
        "opensre.egg-info",
        "packaging",
        "tests",
        "venv",
    }
)

_FORBIDDEN_RULES: dict[str, set[str]] = {
    "core": {"integrations", "tools", "cli"},
    "integrations": {"tools", "cli"},
    "tools": {"cli"},
    "platform": {"core", "integrations", "tools", "cli"},
    "config": {"core", "integrations", "tools", "cli", "platform", "infra"},
}

_BASELINE_IGNORES: set[str] = {
    "integrations.hermes.sinks -> tools.watch_dog.alarms",
    "integrations.cli -> cli.wizard.integration_health",
}


def discover_packages(repo_root: Path) -> list[str]:
    """Dynamically discover package roots in the repository."""
    packages = []
    if not repo_root.exists():
        return list(_FORBIDDEN_RULES.keys())
    for child in repo_root.iterdir():
        if (
            child.is_dir()
            and not child.name.startswith(".")
            and child.name not in _SKIP_ROOT_DIRS
            and any(child.rglob("*.py"))
        ):
            packages.append(child.name)
    return sorted(packages)


def get_module_path(file_path: Path, repo_root: Path) -> str:
    """Compute the python module path for a file relative to the repo root."""
    try:
        rel_parts = file_path.relative_to(repo_root).with_suffix("").parts
        module_path = ".".join(rel_parts)
        if rel_parts and rel_parts[-1] == "__init__":
            module_path = ".".join(rel_parts[:-1])
        return module_path
    except ValueError:
        return ""


def resolve_import(module_path: str, imported_module: str, level: int) -> str:
    """Resolve an absolute or relative import to its full absolute module path."""
    if level == 0:
        return imported_module
    parts = module_path.split(".")
    if level >= len(parts):
        return imported_module
    base = ".".join(parts[:-level])
    if base:
        if imported_module:
            return f"{base}.{imported_module}"
        return base
    return imported_module


def is_compatibility_shim(file_path: str, content: str) -> bool:
    """Check if a Python file is a compatibility-only forwarding module."""
    if Path(file_path).name == "__init__.py":
        return False
    try:
        tree = ast.parse(content)
    except Exception:
        return False

    has_import_or_alias = False

    for node in tree.body:
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            has_import_or_alias = True
        elif isinstance(node, ast.Assign):
            is_alias = True
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id == "__all__":
                    continue
                elif isinstance(target, ast.Name):
                    if not isinstance(node.value, (ast.Name, ast.Attribute)):
                        is_alias = False
                else:
                    is_alias = False
            if is_alias:
                has_import_or_alias = True
            else:
                return False
        elif (
            isinstance(node, ast.Expr)
            and isinstance(node.value, ast.Constant)
            and isinstance(node.value.value, str)
            or isinstance(node, ast.Pass)
        ):
            continue
        else:
            return False

    return has_import_or_alias


def analyze_misplaced(file_path: Path, repo_root: Path, content: str) -> tuple[bool, str]:
    """Check if a module is misplaced according to architectural guidelines."""
    # Check for vendors/ or services/ in path
    parts = file_path.relative_to(repo_root).parts
    if "vendors" in parts or "services" in parts:
        return True, "Do not add or import top-level or nested 'vendors/' or 'services/' packages."

    # Parse AST to check for tool decorators or base classes
    has_tool_decorator = False
    has_basetool_subclass = False
    try:
        tree = ast.parse(content)
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                for dec in node.decorator_list:
                    if (isinstance(dec, ast.Name) and dec.id == "tool") or (
                        isinstance(dec, ast.Call)
                        and isinstance(dec.func, ast.Name)
                        and dec.func.id == "tool"
                    ):
                        has_tool_decorator = True
            elif isinstance(node, ast.ClassDef):
                for base in node.bases:
                    if isinstance(base, ast.Name) and base.id == "BaseTool":
                        has_basetool_subclass = True
    except Exception:
        pass

    # Tools must reside under tools/ directory
    is_tool_code = has_tool_decorator or has_basetool_subclass
    in_tools_dir = parts[0] == "tools"
    if is_tool_code and not in_tools_dir:
        return (
            True,
            "Tool definitions (functions decorated with @tool or classes inheriting from BaseTool) must reside inside the 'tools/' package.",
        )

    # Client/Verifier/Config code in tools is misplaced
    if in_tools_dir and "utils" not in parts:
        filename = file_path.name
        is_client_file = (
            filename == "client.py"
            or filename == "verifier.py"
            or filename == "config.py"
            or filename.endswith("_client.py")
            or filename.endswith("_verifier.py")
            or filename.endswith("_config.py")
        )
        if is_client_file:
            return True, (
                "Treat 'integrations/' as the canonical user/config and external-client boundary, "
                "and 'tools/' as the canonical agent-callable boundary. Client, config, or verifier "
                "logic should reside in 'integrations/' rather than 'tools/'."
            )

    return False, ""


@tool(
    name="find_architecture_violations",
    source="knowledge",
    description=(
        "Scan the codebase to detect architectural violations (dependency direction, "
        "oversized files, compatibility shims, misplaced modules) and propose atomic refactor tasks."
    ),
    surfaces=("investigation", "chat"),
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
        found = False
        for parent in [current] + list(current.parents):
            if (parent / "pyproject.toml").exists() or (parent / ".git").exists():
                root_path = parent
                found = True
                break
        if not found:
            root_path = current.parents[2]

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
                    resolved = resolve_import(module_path, module_name, node.level)
                    top = resolved.split(".", 1)[0]
                    if top in packages:
                        file_imports.append((resolved, node.lineno))

            for imported_module, lineno in file_imports:
                imported_pkg = imported_module.split(".", 1)[0]
                if source_pkg in _FORBIDDEN_RULES and imported_pkg in _FORBIDDEN_RULES[source_pkg]:
                    # Check if ignored by baseline
                    is_ignored = False
                    for ignore_edge in _BASELINE_IGNORES:
                        ign_src, ign_dst = [p.strip() for p in ignore_edge.split("->")]
                        if module_path.startswith(ign_src) and imported_module.startswith(ign_dst):
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
