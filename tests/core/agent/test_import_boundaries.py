"""Import-boundary tests for the surface-agnostic agent engine."""

from __future__ import annotations

import ast
from pathlib import Path


def _repo_root() -> Path:
    for parent in Path(__file__).resolve().parents:
        if (parent / "pyproject.toml").is_file():
            return parent
    return Path(__file__).resolve().parents[3]


def test_core_agent_does_not_import_interactive_shell() -> None:
    root = _repo_root()
    offenders: list[str] = []
    for path in sorted((root / "core" / "agent").rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name == "interactive_shell" or alias.name.startswith(
                        "interactive_shell."
                    ):
                        offenders.append(str(path.relative_to(root)))
            elif isinstance(node, ast.ImportFrom):
                module = node.module or ""
                if module == "interactive_shell" or module.startswith("interactive_shell."):
                    offenders.append(str(path.relative_to(root)))

    assert not offenders, "\n".join(offenders)
