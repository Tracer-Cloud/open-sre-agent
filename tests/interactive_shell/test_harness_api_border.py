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

from pathlib import Path

from tests.shared.harness_doors import assert_ledger_only_shrinks, deep_harness_imports_under

REPO_ROOT = Path(__file__).resolve().parents[2]

#: Harness submodules surfaces/ still imports directly. Shrink-only — and now
#: empty: every harness name surfaces/ uses comes through a door.
_KNOWN_DEEP_IMPORTS: frozenset[str] = frozenset()


def test_surfaces_deep_imports_only_shrink() -> None:
    imported = deep_harness_imports_under(REPO_ROOT / "surfaces")
    assert_ledger_only_shrinks(imported, _KNOWN_DEEP_IMPORTS, tier="surfaces/")
