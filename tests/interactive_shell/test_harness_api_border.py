"""Surfaces reach the agent harness through its curated surface — or the ledger.

Twin of ``gateway/tests/test_harness_api_border.py``. The interactive shell is
a thicker host than the gateway — it assembles the agent stack itself — so its
existing deep imports are pinned in a ledger rather than migrated wholesale:
hoisting all ~29 modules into the curated ``core.agent_harness.__init__`` table
at once would un-curate it.

The ledger is asserted with exact equality in both directions, like the
transport conformance ledger: a *new* deep-import module fails immediately, and
a module that stops being imported must be deleted here, so the set can only
shrink. Curating a name into the export table (and migrating its importers) is
the way an entry gets removed.
"""

from __future__ import annotations

import ast
from pathlib import Path

from tests.shared.harness_doors import PUBLIC_DOORS

REPO_ROOT = Path(__file__).resolve().parents[2]

_FORBIDDEN_PREFIX = "core.agent_harness."
#: Modules a host may import the harness through; anything else under the
#: prefix is a deep import.
_DYNAMIC_IMPORTERS = frozenset({"import_module", "__import__"})


def _is_public_door(module: str) -> bool:
    return module in PUBLIC_DOORS


#: Harness submodules surfaces/ still imports directly. Shrink-only.
_KNOWN_DEEP_IMPORTS = frozenset(
    {
        "core.agent_harness.llm_resolution",
        "core.agent_harness.prompts.skills.loader",
        "core.agent_harness.session.persistence.jsonl_store",
        "core.agent_harness.session.persistence.wal_recovery",
        "core.agent_harness.tools.tool_context",
        "core.agent_harness.turns.action_driver",
        "core.agent_harness.turns.transcript_compaction",
        "core.agent_harness.turns.turn_plan",
    }
)


def _surface_sources() -> list[Path]:
    return [
        path
        for path in sorted((REPO_ROOT / "surfaces").rglob("*.py"))
        if "__pycache__" not in path.parts
    ]


def _call_name(node: ast.Call) -> str:
    func = node.func
    if isinstance(func, ast.Attribute):
        return func.attr
    if isinstance(func, ast.Name):
        return func.id
    return ""


def _deep_modules(tree: ast.AST) -> set[str]:
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found.update(
                alias.name
                for alias in node.names
                if alias.name.startswith(_FORBIDDEN_PREFIX) and not _is_public_door(alias.name)
            )
        elif isinstance(node, ast.ImportFrom):
            module = node.module or ""
            if (
                node.level == 0
                and module.startswith(_FORBIDDEN_PREFIX)
                and not _is_public_door(module)
            ):
                found.add(module)
        elif isinstance(node, ast.Call) and _call_name(node) in _DYNAMIC_IMPORTERS:
            for arg in node.args[:1]:
                if (
                    isinstance(arg, ast.Constant)
                    and isinstance(arg.value, str)
                    and arg.value.startswith(_FORBIDDEN_PREFIX)
                    and not _is_public_door(arg.value)
                ):
                    found.add(arg.value)
    return found


def test_surfaces_deep_imports_only_shrink() -> None:
    imported: set[str] = set()
    for path in _surface_sources():
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        imported |= _deep_modules(tree)

    new = sorted(imported - _KNOWN_DEEP_IMPORTS)
    assert new == [], (
        f"surfaces/ grew new harness deep imports: {new}. Import the name from "
        "core.agent_harness (embedder API) or core.agent_harness.spi (host-adapter "
        "API); curate it into the right door if it is genuinely public."
    )
    migrated = sorted(_KNOWN_DEEP_IMPORTS - imported)
    assert migrated == [], (
        f"surfaces/ no longer imports {migrated} directly — remove those entries "
        "from _KNOWN_DEEP_IMPORTS so the ledger keeps shrinking."
    )
