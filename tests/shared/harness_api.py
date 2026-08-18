"""The API modules of ``core.agent_harness`` and the scanner that enforces them.

The harness is imported through a fixed set of API modules. This module is the
single definition of that set, shared by the border tests (which allow these
modules and no others) and by ``tests/core/agent_harness/test_harness_api.py``
(which pins their exported names), so the two cannot diverge.
"""

from __future__ import annotations

import ast
import functools
import importlib
from pathlib import Path

HARNESS_PACKAGE = "core.agent_harness"

#: The ``core.agent_harness.spi`` role modules. Each is an API module; the
#: ``spi`` package itself is not.
SPI_ROLES: frozenset[str] = frozenset(
    {
        "session_goal",
        "session_state",
        "cancel",
        "accounting",
        "prompt_chrome",
        "integrations",
        "grounding",
        "defaults",
    }
)

#: Every module through which the harness may be imported. Listed by exact name
#: rather than by prefix, so a future internal module under ``spi/`` does not
#: become part of the API by accident.
API_MODULES: frozenset[str] = frozenset(
    {
        HARNESS_PACKAGE,
        f"{HARNESS_PACKAGE}.ports",
        f"{HARNESS_PACKAGE}.runtime",
        f"{HARNESS_PACKAGE}.tools",
    }
    | {f"{HARNESS_PACKAGE}.spi.{role}" for role in SPI_ROLES}
)

_DYNAMIC_IMPORT_FUNCTIONS = frozenset({"import_module", "__import__"})


def is_api_module(module: str) -> bool:
    """Return True when ``module`` is one of the harness API modules."""
    return module in API_MODULES


def is_harness_module(module: str) -> bool:
    """Return True when ``module`` is the harness package or any module inside it."""
    return module == HARNESS_PACKAGE or module.startswith(HARNESS_PACKAGE + ".")


def python_sources(root: Path, *, exclude_parts: frozenset[str] = frozenset()) -> list[Path]:
    """Return the Python files under ``root``, excluding ``__pycache__`` and ``exclude_parts``."""
    excluded = exclude_parts | {"__pycache__"}
    return [path for path in sorted(root.rglob("*.py")) if not (excluded & set(path.parts))]


def _called_function_name(node: ast.Call) -> str:
    func = node.func
    if isinstance(func, ast.Attribute):
        return func.attr
    if isinstance(func, ast.Name):
        return func.id
    return ""


@functools.cache
def _exported_names(module: str) -> frozenset[str]:
    """The names an API module exports in ``__all__``."""
    return frozenset(importlib.import_module(module).__all__)


def internal_harness_imports(tree: ast.AST) -> set[str]:
    """Return the harness modules ``tree`` imports that are not API modules.

    Covers ``import x``, ``from x import y`` and dynamic imports
    (``import_module("x")``). For ``from <api module> import y``, a name that
    the API module does not export in ``__all__`` is treated as an import of
    the internal submodule ``<api module>.y``.
    """
    internal: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            internal.update(
                alias.name
                for alias in node.names
                if is_harness_module(alias.name) and not is_api_module(alias.name)
            )
        elif isinstance(node, ast.ImportFrom):
            module = node.module or ""
            if node.level != 0 or not is_harness_module(module):
                continue
            if not is_api_module(module):
                internal.add(module)
                continue
            exported = _exported_names(module)
            internal.update(
                f"{module}.{alias.name}" for alias in node.names if alias.name not in exported
            )
        elif (
            isinstance(node, ast.Call) and _called_function_name(node) in _DYNAMIC_IMPORT_FUNCTIONS
        ):
            for argument in node.args[:1]:
                if (
                    isinstance(argument, ast.Constant)
                    and isinstance(argument.value, str)
                    and is_harness_module(argument.value)
                    and not is_api_module(argument.value)
                ):
                    internal.add(argument.value)
    return internal


def internal_harness_imports_under(
    *roots: Path, exclude_parts: frozenset[str] = frozenset()
) -> set[str]:
    """Return every internal harness import across the Python sources under ``roots``."""
    imported: set[str] = set()
    for root in roots:
        for path in python_sources(root, exclude_parts=exclude_parts):
            source = path.read_text(encoding="utf-8")
            imported |= internal_harness_imports(ast.parse(source, filename=str(path)))
    return imported


def assert_internal_imports_match_allowlist(
    imported: set[str], allowlist: frozenset[str], *, package: str
) -> None:
    """Assert ``imported`` equals ``allowlist`` exactly.

    A module not on the allowlist is a new internal dependency and fails; a
    module on the allowlist that is no longer imported must be removed from it,
    so the allowlist can only shrink.
    """
    added = sorted(imported - allowlist)
    assert added == [], (
        f"{package} imports harness internals not on the allowlist: {added}. "
        f"Import through an API module ({', '.join(sorted(API_MODULES))}) "
        "or export the name from the appropriate one."
    )
    stale = sorted(allowlist - imported)
    assert stale == [], (
        f"{package} no longer imports {stale}; remove them from the allowlist so it keeps shrinking."
    )


__all__ = [
    "HARNESS_PACKAGE",
    "API_MODULES",
    "SPI_ROLES",
    "assert_internal_imports_match_allowlist",
    "internal_harness_imports",
    "internal_harness_imports_under",
    "is_harness_module",
    "is_api_module",
    "python_sources",
]
