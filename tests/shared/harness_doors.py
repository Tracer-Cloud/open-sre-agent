"""The harness's public import doors, shared by the border tests and the API pin.

One list so the border tests (which allow these modules) and
``tests/core/agent_harness/test_public_api.py`` (which pins their contents)
cannot drift apart: a role added to one is a role added to both.
"""

from __future__ import annotations

import ast
from pathlib import Path

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

#: Every module a host may import the harness through. Exact names — no prefix
#: rule, so a future internal module under ``spi/`` is not public by accident.
PUBLIC_DOORS: frozenset[str] = frozenset(
    {
        "core.agent_harness",
        "core.agent_harness.ports",
        "core.agent_harness.runtime",
        "core.agent_harness.tools",
    }
    | {f"core.agent_harness.spi.{role}" for role in SPI_ROLES}
)

_FORBIDDEN_PREFIX = "core.agent_harness."
_DYNAMIC_IMPORTERS = frozenset({"import_module", "__import__"})


def is_public_door(module: str) -> bool:
    """True for a module a host or tool may import the harness through."""
    return module in PUBLIC_DOORS


def python_sources(root: Path, *, exclude_parts: frozenset[str] = frozenset()) -> list[Path]:
    """Python files under ``root``, skipping ``__pycache__`` and any path containing an excluded part."""
    skip = exclude_parts | {"__pycache__"}
    return [p for p in sorted(root.rglob("*.py")) if not (skip & set(p.parts))]


def _call_name(node: ast.Call) -> str:
    func = node.func
    if isinstance(func, ast.Attribute):
        return func.attr
    if isinstance(func, ast.Name):
        return func.id
    return ""


def deep_harness_imports(tree: ast.AST) -> set[str]:
    """Harness modules ``tree`` imports that are not doors — static and dynamic (``import_module``)."""
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found.update(
                alias.name
                for alias in node.names
                if alias.name.startswith(_FORBIDDEN_PREFIX) and not is_public_door(alias.name)
            )
        elif isinstance(node, ast.ImportFrom):
            module = node.module or ""
            if (
                node.level == 0
                and module.startswith(_FORBIDDEN_PREFIX)
                and not is_public_door(module)
            ):
                found.add(module)
        elif isinstance(node, ast.Call) and _call_name(node) in _DYNAMIC_IMPORTERS:
            for arg in node.args[:1]:
                if (
                    isinstance(arg, ast.Constant)
                    and isinstance(arg.value, str)
                    and arg.value.startswith(_FORBIDDEN_PREFIX)
                    and not is_public_door(arg.value)
                ):
                    found.add(arg.value)
    return found


def deep_harness_imports_under(
    *roots: Path, exclude_parts: frozenset[str] = frozenset()
) -> set[str]:
    """Every deep harness import across the Python sources under ``roots``."""
    imported: set[str] = set()
    for root in roots:
        for path in python_sources(root, exclude_parts=exclude_parts):
            imported |= deep_harness_imports(
                ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            )
    return imported


def assert_ledger_only_shrinks(imported: set[str], known: frozenset[str], *, tier: str) -> None:
    """Exact equality both ways: a new deep import fails; a retired one must be removed."""
    new = sorted(imported - known)
    assert new == [], (
        f"{tier} grew new harness deep imports: {new}. Import the name through a door "
        "(core.agent_harness, .ports, .spi.<role>, .runtime, .tools); curate it into "
        "the right door if it is genuinely public."
    )
    migrated = sorted(known - imported)
    assert migrated == [], (
        f"{tier} no longer imports {migrated} directly — remove those entries from the "
        "ledger so it keeps shrinking."
    )


__all__ = [
    "PUBLIC_DOORS",
    "SPI_ROLES",
    "assert_ledger_only_shrinks",
    "deep_harness_imports",
    "deep_harness_imports_under",
    "is_public_door",
    "python_sources",
]
