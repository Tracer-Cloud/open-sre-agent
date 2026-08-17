"""The tool tier reaches the agent harness through its doors — or the ledger.

Third border after the shell and gateway ones: ``tools/``, ``integrations/`` and
``platform/`` may import the harness only through a door. ``bootstrap/`` is the
composition root — the one package that wires harness internals — and is exempt.

The ledger is exact both ways, so it can only shrink: a new deep import fails
immediately, and a retired one must be deleted here.
"""

from __future__ import annotations

from pathlib import Path

from tests.shared.harness_doors import assert_ledger_only_shrinks, deep_harness_imports_under

REPO_ROOT = Path(__file__).resolve().parents[2]

#: Harness submodules the tool tier still imports directly. Shrink-only.
_KNOWN_DEEP_IMPORTS: frozenset[str] = frozenset()


def test_tool_tier_deep_imports_only_shrink() -> None:
    imported = deep_harness_imports_under(
        REPO_ROOT / "tools", REPO_ROOT / "integrations", REPO_ROOT / "platform"
    )
    assert_ledger_only_shrinks(
        imported, _KNOWN_DEEP_IMPORTS, tier="tools/, integrations/, platform/"
    )
