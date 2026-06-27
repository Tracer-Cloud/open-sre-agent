"""Import-boundary tests for shared state contracts."""

from __future__ import annotations

import ast
from pathlib import Path


def _repo_root() -> Path:
    for parent in Path(__file__).resolve().parents:
        if (parent / "pyproject.toml").is_file():
            return parent
    return Path(__file__).resolve().parents[3]


def test_context_state_stays_dependency_light() -> None:
    root = _repo_root()
    forbidden = (
        "core." + "orchestration",
        "integrations",
        "core.domain.alerts.normalization",
    )
    offenders: list[str] = []
    for path in sorted((root / "context" / "state").rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            module_names: list[str] = []
            if isinstance(node, ast.Import):
                module_names.extend(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom):
                module_names.append(node.module or "")
            for module_name in module_names:
                for import_path in forbidden:
                    if module_name == import_path or module_name.startswith(f"{import_path}."):
                        offenders.append(f"{path.relative_to(root)} imports {import_path}")

    assert not offenders, "\n".join(offenders)


def test_old_core_domain_state_import_path_is_removed() -> None:
    root = _repo_root()
    old_state_module = ".".join(("core", "domain", "state"))
    offenders: list[str] = []
    for path in sorted(root.rglob("*.py")):
        if ".venv" in path.parts:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            module_names: list[str] = []
            if isinstance(node, ast.Import):
                module_names.extend(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom):
                module_names.append(node.module or "")
            for module_name in module_names:
                if module_name == old_state_module or module_name.startswith(
                    f"{old_state_module}."
                ):
                    offenders.append(str(path.relative_to(root)))
                if module_name == "core.domain":
                    imported_names = [
                        alias.name for alias in node.names if isinstance(node, ast.ImportFrom)
                    ]
                    if "state" in imported_names:
                        offenders.append(str(path.relative_to(root)))

    assert not offenders, "\n".join(offenders)
