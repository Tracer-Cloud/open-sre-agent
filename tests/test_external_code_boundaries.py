"""Architecture guards for external-system package boundaries."""

from __future__ import annotations

import ast
import subprocess
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FORBIDDEN_TOP_LEVEL_PACKAGES = frozenset({"services", "vendors"})


def _tracked_files(*pathspecs: str) -> list[Path]:
    result = subprocess.run(
        ["git", "ls-files", "--", *pathspecs],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    return [ROOT / line for line in result.stdout.splitlines()]


def _forbidden_imports(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    imports: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imports.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            imports.add(node.module)
    return {module for module in imports if module.split(".", 1)[0] in FORBIDDEN_TOP_LEVEL_PACKAGES}


def test_no_tracked_python_imports_from_removed_external_packages() -> None:
    leaks = {
        str(path.relative_to(ROOT)): sorted(_forbidden_imports(path))
        for path in _tracked_files("*.py")
        if _forbidden_imports(path)
    }
    assert leaks == {}


def test_removed_external_packages_have_no_tracked_files() -> None:
    assert _tracked_files("services", "services/**", "vendors", "vendors/**") == []


def test_tools_registry_does_not_scan_removed_external_packages() -> None:
    registry_source = (ROOT / "tools/registry.py").read_text(encoding="utf-8")
    assert "vendors" not in registry_source
    assert "services" not in registry_source


def test_pyproject_does_not_package_removed_external_packages() -> None:
    pyproject = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    package_includes = set(pyproject["tool"]["setuptools"]["packages"]["find"]["include"])
    vulture_paths = set(pyproject["tool"]["vulture"]["paths"])

    assert "services*" not in package_includes
    assert "vendors*" not in package_includes
    assert "services" not in vulture_paths
    assert "vendors" not in vulture_paths
