"""Gateway reaches the agent harness only through its curated public surface.

``core.agent_harness.__init__`` is the harness's host API — a curated, lazily
resolved export table that AGENTS.md calls "the package's public surface".
Gateway code had grown imports of eleven internal submodules
(``turns.host_cancel``, ``session_goal.run_until``, ``accounting…``), so any
harness-internal rename broke the gateway. One import path makes the coupling
surface explicit and reviewable: widening it means editing the harness's own
export table, not quietly reaching deeper.

Scanned as AST, not text: regexes miss line-continuation imports, unusual
module-name characters, and dynamic ``importlib.import_module(...)`` calls.
"""

from __future__ import annotations

import ast
from pathlib import Path

from tests.shared.harness_doors import PUBLIC_DOORS

REPO_ROOT = Path(__file__).resolve().parents[2]

_FORBIDDEN_PREFIX = "core.agent_harness."
#: Modules a host may import the harness through; anything else under the
#: prefix is a deep import.

#: Callables whose string argument names a module to import at runtime.
_DYNAMIC_IMPORTERS = frozenset({"import_module", "__import__"})


def _is_public_door(module: str) -> bool:
    return module in PUBLIC_DOORS


def _gateway_sources() -> list[Path]:
    return [
        path
        for path in sorted((REPO_ROOT / "gateway").rglob("*.py"))
        if "__pycache__" not in path.parts and "tests" not in path.parts
    ]


def _call_name(node: ast.Call) -> str:
    func = node.func
    if isinstance(func, ast.Attribute):
        return func.attr
    if isinstance(func, ast.Name):
        return func.id
    return ""


def _submodule_imports(tree: ast.AST) -> list[str]:
    hits: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            hits.extend(
                f"import {alias.name}"
                for alias in node.names
                if alias.name.startswith(_FORBIDDEN_PREFIX) and not _is_public_door(alias.name)
            )
        elif isinstance(node, ast.ImportFrom):
            # ``level`` > 0 is a relative import and cannot name the harness.
            module = node.module or ""
            if (
                node.level == 0
                and module.startswith(_FORBIDDEN_PREFIX)
                and not _is_public_door(module)
            ):
                hits.append(f"from {module} import …")
        elif isinstance(node, ast.Call) and _call_name(node) in _DYNAMIC_IMPORTERS:
            for arg in node.args[:1]:
                if (
                    isinstance(arg, ast.Constant)
                    and isinstance(arg.value, str)
                    and arg.value.startswith(_FORBIDDEN_PREFIX)
                    and not _is_public_door(arg.value)
                ):
                    hits.append(f"import_module({arg.value!r})")
    return hits


def test_gateway_imports_the_harness_only_through_its_public_surface() -> None:
    offenders: dict[str, list[str]] = {}
    for path in _gateway_sources():
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        hits = _submodule_imports(tree)
        if hits:
            offenders[str(path.relative_to(REPO_ROOT))] = hits

    assert offenders == {}, (
        "gateway imports agent-harness internals; import the name from "
        "core.agent_harness instead (add it to the curated export table in "
        f"core/agent_harness/__init__.py if it is genuinely host-facing): {offenders}"
    )


def test_an_internal_module_under_spi_is_not_a_door() -> None:
    """Only the curated roles are public; a future ``spi/`` internal is a deep import."""
    assert _is_public_door("core.agent_harness.spi.session_goal")
    assert not _is_public_door("core.agent_harness.spi.internal_helper")
    assert not _is_public_door("core.agent_harness.spi.session_goal.impl")
    assert not _is_public_door("core.agent_harness.spi")
