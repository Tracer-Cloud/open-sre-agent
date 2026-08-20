"""Quality gate: no new ``Callable[..., Any]`` under harness ports and shell runtime.

Pin today's count as a baseline so any new ``Callable[..., Any]`` annotation in
the covered paths forces an explicit decision: either tighten the signature or
bump the baseline with a clear justification in the PR description.
"""

from __future__ import annotations

import ast
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]

_COVERED_PATHS: tuple[str, ...] = (
    "core/agent_harness/ports.py",
    "platform/harness_ports.py",
    # Entire runtime directory tree
    "surfaces/interactive_shell/runtime/",
)

# Pinned baseline as of 2026-08-20: a single ``BuildCliClientFn = Callable[..., Any]``
# in ``platform/harness_ports.py``.  If you legitimately need a new
# ``Callable[..., Any]`` in a covered path, bump this number and explain why
# in the PR description.
_EXPECTED_BASELINE: int = 1


def _is_callable_any(node: ast.AST) -> bool:
    """True for a ``Callable[..., Any]`` annotation (incl. ``typing.Callable``)."""
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


def _count_in_source(source: str) -> int:
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return 0
    return sum(1 for node in ast.walk(tree) if _is_callable_any(node))


def _count_in_file(path: Path) -> int:
    return _count_in_source(path.read_text(encoding="utf-8"))


def _count_in_dir(root: Path) -> int:
    total = 0
    for py_file in sorted(root.rglob("*.py")):
        total += _count_in_file(py_file)
    return total


def test_no_new_callable_any_in_covered_paths() -> None:
    total = 0
    for rel_path in _COVERED_PATHS:
        full = _REPO_ROOT / rel_path
        if not full.exists():
            continue
        if full.is_dir():
            total += _count_in_dir(full)
        else:
            total += _count_in_file(full)

    assert total <= _EXPECTED_BASELINE, (
        f"Expected at most {_EXPECTED_BASELINE} ``Callable[..., Any]`` "
        f"annotations in covered paths, found {total}. If this increase is "
        f"intentional, bump _EXPECTED_BASELINE and explain the rationale in "
        f"the PR description."
    )
