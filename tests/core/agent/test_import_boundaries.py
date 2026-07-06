"""Import-boundary tests for the surface-agnostic agent engine."""

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
    package_root: Path,
    forbidden_modules: frozenset[str],
    forbidden_prefixes: tuple[str, ...],
) -> list[str]:
    offenders: list[str] = []
    for path in sorted(package_root.rglob("*.py")):
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


def test_core_agent_harness_does_not_import_interactive_shell() -> None:
    root = _repo_root()
    offenders = _collect_surface_import_offenders(
        root,
        package_root=root / "core" / "agent_harness",
        forbidden_modules=frozenset({"interactive_shell", "surfaces.interactive_shell"}),
        forbidden_prefixes=("surfaces.interactive_shell.",),
    )
    assert not offenders, "\n".join(offenders)


def test_core_agent_harness_does_not_import_surfaces_cli() -> None:
    root = _repo_root()
    offenders = _collect_surface_import_offenders(
        root,
        package_root=root / "core" / "agent_harness",
        forbidden_modules=frozenset({"surfaces.cli"}),
        forbidden_prefixes=("surfaces.cli.",),
    )
    assert not offenders, "\n".join(offenders)


def _type_checking_import_lines(tree: ast.Module) -> set[int]:
    """Line numbers of imports inside an ``if TYPE_CHECKING:`` block (typing-only)."""
    lines: set[int] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.If):
            continue
        test = node.test
        is_guard = (isinstance(test, ast.Name) and test.id == "TYPE_CHECKING") or (
            isinstance(test, ast.Attribute) and test.attr == "TYPE_CHECKING"
        )
        if is_guard:
            # Walk only the ``if`` body — an import in the ``else:`` branch runs at
            # runtime (TYPE_CHECKING is False then), so it must not be exempted.
            for stmt in node.body:
                for child in ast.walk(stmt):
                    if isinstance(child, ast.Import | ast.ImportFrom):
                        lines.add(child.lineno)
    return lines


def test_core_agent_has_no_runtime_import_of_agent_harness() -> None:
    """``core/agent`` is the algorithm; the harness facades live in
    ``agent_harness``. A runtime import here would reintroduce the import cycle
    the retired ``Agent`` facades needed ``importlib`` to dodge. A TYPE_CHECKING
    reference (for an annotation) is allowed."""
    root = _repo_root()
    offenders: list[str] = []
    for path in sorted((root / "core" / "agent").rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        skip = _type_checking_import_lines(tree)
        for node in ast.walk(tree):
            if not isinstance(node, ast.Import | ast.ImportFrom) or node.lineno in skip:
                continue
            targets = (
                [node.module or ""]
                if isinstance(node, ast.ImportFrom)
                else [alias.name for alias in node.names]
            )
            if any(target.startswith("core.agent_harness") for target in targets):
                offenders.append(f"{path.relative_to(root)}:{node.lineno}")
    assert not offenders, "core/agent runtime-imports agent_harness:\n" + "\n".join(offenders)
