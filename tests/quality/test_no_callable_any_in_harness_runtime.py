"""Quality gate: no new ``Callable[..., Any]`` under harness ports and shell runtime.

Pin today's count as a baseline so any new ``Callable[..., Any]`` annotation in
the covered paths forces an explicit decision: either tighten the signature or
bump the baseline with a clear justification in the PR description.

The count is computed from the AST, not raw text, so comments, docstrings, and
ordinary string literals are ignored. Quoted (forward-reference) annotations
such as ``cb: "Callable[..., Any]"`` are still counted because their string is
parsed back into an annotation expression.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[2]

_COVERED_PATHS: tuple[str, ...] = (
    "core/agent_harness/ports.py",
    "infrastructure/harness_ports.py",
    # Entire runtime directory tree
    "surfaces/interactive_shell/runtime/",
)

# Pinned baseline as of 2026-08-20: a single ``BuildCliClientFn = Callable[..., Any]``
# in ``infrastructure/harness_ports.py``. If you legitimately need a new
# ``Callable[..., Any]`` in a covered path, bump this number and explain why in
# the PR description. If you remove the last occurrence, lower it too — the gate
# requires an exact match so a stale baseline cannot hide a future regression.
_EXPECTED_BASELINE: int = 1


def _is_callable_any(node: ast.AST) -> bool:
    """True for a ``Callable[..., Any]`` subscript (incl. ``typing.Callable``)."""
    if not isinstance(node, ast.Subscript):
        return False
    value = node.value
    if isinstance(value, ast.Name):
        if value.id != "Callable":
            return False
    elif isinstance(value, ast.Attribute):
        if value.attr != "Callable":
            return False
    else:
        return False

    args = node.slice
    if not isinstance(args, ast.Tuple) or len(args.elts) != 2:
        return False
    params, ret = args.elts
    if not (isinstance(params, ast.Constant) and params.value is Ellipsis):
        return False
    if isinstance(ret, ast.Name):
        return ret.id == "Any"
    if isinstance(ret, ast.Attribute):
        return ret.attr == "Any"
    return False


def _is_type_alias(node: ast.AST | None) -> bool:
    """True for a bare ``TypeAlias`` / ``typing.TypeAlias`` / ``typing_extensions.TypeAlias``."""
    if isinstance(node, ast.Name):
        return node.id == "TypeAlias"
    if isinstance(node, ast.Attribute):
        return node.attr == "TypeAlias"
    return False


def _string_annotation_nodes(tree: ast.AST) -> list[ast.Constant]:
    """String constants that appear at annotation positions.

    A quoted (forward-reference) annotation is a ``str`` where the AST would
    otherwise place an expression: function arguments, return annotations,
    annotated assignments, and ``TypeAlias`` values. Ordinary string literals
    (assignments, docstrings, prose) are deliberately excluded.
    """
    nodes: list[ast.Constant] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.AnnAssign):
            nodes.append(node.annotation)
            if _is_type_alias(node.annotation):
                nodes.append(node.value)
        elif isinstance(node, ast.arg):
            nodes.append(node.annotation)
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            nodes.append(node.returns)
    return [n for n in nodes if isinstance(n, ast.Constant) and isinstance(n.value, str)]


def _is_quoted_callable_any(node: ast.Constant) -> bool:
    """True for a string annotation that parses back to ``Callable[..., Any]``."""
    text = node.value.strip()
    if "Callable" not in text:
        return False
    try:
        expr = ast.parse(text, mode="eval").body
    except SyntaxError:
        return False
    return _is_callable_any(expr)


def _count_in_source(source: str) -> int:
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return 0
    total = sum(1 for node in ast.walk(tree) if _is_callable_any(node))
    total += sum(1 for node in _string_annotation_nodes(tree) if _is_quoted_callable_any(node))
    return total


def _count_in_file(path: Path) -> int:
    return _count_in_source(path.read_text(encoding="utf-8"))


def _count_in_dir(root: Path) -> int:
    total = 0
    for py_file in sorted(root.rglob("*.py")):
        total += _count_in_file(py_file)
    return total


def test_no_new_callable_any_in_covered_paths() -> None:
    missing = [p for p in _COVERED_PATHS if not (_REPO_ROOT / p).exists()]
    assert not missing, (
        "A covered path no longer exists. Update _COVERED_PATHS so the gate "
        "still covers the renamed/moved files:\n" + "\n".join(f"- {p}" for p in missing)
    )

    total = 0
    for rel_path in _COVERED_PATHS:
        full = _REPO_ROOT / rel_path
        if full.is_dir():
            total += _count_in_dir(full)
        else:
            total += _count_in_file(full)

    assert total == _EXPECTED_BASELINE, (
        f"Expected exactly {_EXPECTED_BASELINE} ``Callable[..., Any]`` "
        f"annotations in covered paths, found {total}. If you added one, "
        f"tighten the signature or bump _EXPECTED_BASELINE with a justification "
        f"in the PR description. If you removed one, lower _EXPECTED_BASELINE "
        f"so it cannot silently regress."
    )


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        ("x: Callable[..., Any]", 1),
        ('x: "Callable[..., Any]"', 1),
        ('def f(cb: "Callable[..., Any]") -> None: ...', 1),
        ('def f() -> "Callable[..., Any]": ...', 1),
        ('Alias: TypeAlias = "Callable[..., Any]"', 1),
        ("from typing import Callable, Any\nx: Callable[..., Any]", 1),
        ("import typing\nx: typing.Callable[..., typing.Any]", 1),
        ("# Callable[..., Any]", 0),
        ('"""Callable[..., Any]"""', 0),
        ('x = "Callable[..., Any]"', 0),
        ("x: Callable[[str], Any]", 0),
        ("x: Callable[..., str]", 0),
        ("def f() -> None: ...", 0),
    ],
)
def test_count_in_source(source: str, expected: int) -> None:
    assert _count_in_source(source) == expected
