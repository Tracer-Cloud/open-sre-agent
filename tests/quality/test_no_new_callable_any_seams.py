"""Pin ``Callable[..., Any]`` occurrences under the harness ports and shell runtime.

Untyped callable seams keep reappearing in ``core/agent_harness/ports.py``,
``platform/harness_ports.py``, and ``surfaces/interactive_shell/runtime/``.
Type the seam instead of widening it — a new ``Callable[..., Any]`` under
these paths must raise the pinned baseline explicitly, not slip in silently.
"""

from __future__ import annotations

import ast
from pathlib import Path

from tests.shared.product_sources import product_python_files

_REPO_ROOT = Path(__file__).resolve().parents[2]
_COVERED_FILES = (
    "core/agent_harness/ports.py",
    "platform/harness_ports.py",
)
_COVERED_DIRS = ("surfaces/interactive_shell/runtime",)

_BASELINE = 0


def _covered_files() -> list[Path]:
    files = [_REPO_ROOT / rel for rel in _COVERED_FILES if (_REPO_ROOT / rel).is_file()]
    for rel in _COVERED_DIRS:
        files.extend(product_python_files(_REPO_ROOT / rel))
    return files


def _is_callable_name(node: ast.expr) -> bool:
    if isinstance(node, ast.Name):
        return node.id == "Callable"
    return isinstance(node, ast.Attribute) and node.attr == "Callable"


def _is_any_name(node: ast.expr) -> bool:
    if isinstance(node, ast.Name):
        return node.id == "Any"
    return isinstance(node, ast.Attribute) and node.attr == "Any"


def _is_ellipsis(node: ast.expr) -> bool:
    return isinstance(node, ast.Constant) and node.value is Ellipsis


def _subscript_args(node: ast.expr) -> list[ast.expr]:
    return node.elts if isinstance(node, ast.Tuple) else [node]


def _is_callable_any(node: ast.Subscript) -> bool:
    if not _is_callable_name(node.value):
        return False
    args = _subscript_args(node.slice)
    return len(args) == 2 and _is_ellipsis(args[0]) and _is_any_name(args[1])


def _callable_any_offenses(tree: ast.AST) -> list[int]:
    return [
        node.lineno
        for node in ast.walk(tree)
        if isinstance(node, ast.Subscript) and _is_callable_any(node)
    ]


def test_harness_seams_have_no_new_callable_any() -> None:
    offenders: list[str] = []
    for path in _covered_files():
        source = path.read_text(encoding="utf-8")
        try:
            tree = ast.parse(source, filename=str(path))
        except SyntaxError as exc:
            offenders.append(f"{path.relative_to(_REPO_ROOT)}: syntax error: {exc}")
            continue
        for lineno in _callable_any_offenses(tree):
            offenders.append(f"{path.relative_to(_REPO_ROOT)}:{lineno}")

    assert len(offenders) == _BASELINE, (
        f"Callable[..., Any] count under harness ports and shell runtime is "
        f"{len(offenders)}, pinned baseline is {_BASELINE}. Type the new seam "
        "instead of widening it; for a legitimate removal, lower _BASELINE to "
        f"{len(offenders)}:\n" + "\n".join(offenders)
    )
