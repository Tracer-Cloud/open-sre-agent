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

from pathlib import Path

from tests.shared.harness_doors import (
    assert_ledger_only_shrinks,
    is_public_door,
    python_sources,
)

REPO_ROOT = Path(__file__).resolve().parents[2]

#: Harness submodules gateway/ still imports directly. Empty: every harness name
#: the gateway uses comes through a door.
_KNOWN_DEEP_IMPORTS: frozenset[str] = frozenset()


def _gateway_product_sources() -> list[Path]:
    return [p for p in python_sources(REPO_ROOT / "gateway") if "tests" not in p.parts]


def test_gateway_imports_the_harness_only_through_its_public_surface() -> None:
    import ast

    from tests.shared.harness_doors import deep_harness_imports

    imported: set[str] = set()
    for path in _gateway_product_sources():
        imported |= deep_harness_imports(
            ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        )
    assert_ledger_only_shrinks(imported, _KNOWN_DEEP_IMPORTS, tier="gateway/")


def test_an_internal_module_under_spi_is_not_a_door() -> None:
    """Only the curated roles are public; a future ``spi/`` internal is a deep import."""
    assert is_public_door("core.agent_harness.spi.session_goal")
    assert not is_public_door("core.agent_harness.spi.internal_helper")
    assert not is_public_door("core.agent_harness.spi.session_goal.impl")
    assert not is_public_door("core.agent_harness.spi")
