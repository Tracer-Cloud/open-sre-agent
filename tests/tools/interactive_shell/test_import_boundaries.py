"""Import-boundary tests for decoupled interactive-shell action tools."""

from __future__ import annotations

import ast
from pathlib import Path


def _repo_root() -> Path:
    for parent in Path(__file__).resolve().parents:
        if (parent / "pyproject.toml").is_file():
            return parent
    return Path(__file__).resolve().parents[3]


def _collect_surface_import_offenders(
    root: Path,
    *,
    paths: list[Path],
    forbidden_modules: frozenset[str],
    forbidden_prefixes: tuple[str, ...],
) -> list[str]:
    offenders: list[str] = []
    for path in sorted(paths):
        if not path.is_file():
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    name = alias.name
                    if name in forbidden_modules or any(
                        name.startswith(prefix) for prefix in forbidden_prefixes
                    ):
                        offenders.append(str(path.relative_to(root)))
            elif isinstance(node, ast.ImportFrom):
                module = node.module or ""
                if module in forbidden_modules or any(
                    module.startswith(prefix) for prefix in forbidden_prefixes
                ):
                    offenders.append(str(path.relative_to(root)))
    return offenders


def test_t03_runners_do_not_import_interactive_shell_surface() -> None:
    root = _repo_root()
    tools_root = root / "tools" / "interactive_shell"
    scoped_paths = [
        tools_root / "shell" / "runner.py",
        tools_root / "synthetic" / "runner.py",
        tools_root / "implementation" / "claude_code_executor.py",
        tools_root / "actions" / "cli_command.py",
    ]
    offenders = _collect_surface_import_offenders(
        root,
        paths=scoped_paths,
        forbidden_modules=frozenset({"surfaces.interactive_shell"}),
        forbidden_prefixes=("surfaces.interactive_shell.",),
    )
    assert not offenders, "\n".join(sorted(set(offenders)))
