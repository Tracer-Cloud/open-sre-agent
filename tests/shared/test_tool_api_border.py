"""Consumers import the tool tier through its API, and the surface only shrinks.

``core/tool/`` (contract, execution, registry port) and ``core/tool_framework/``
(``@tool``, skill guidance, payload utilities) are one tier to everything above
them. Three doors are open: ``core.tool`` (the contract — what a tool is, how
it runs, where it is registered), ``core.tool_framework.utils`` (schema builders,
MCP readers, availability envelopes), and the ``core.tool_framework`` root, which
exports nothing yet. Everything reaching past a door is listed below, and these
allowlists are the measurement of the seam.

``gateway``, ``surfaces`` and ``infrastructure`` are at zero: they use the contract and
nothing behind it.

Each allowlist is compared exactly in both directions: a new internal import
fails immediately, and an entry no longer imported must be removed. Widening a
door and moving callers onto it is how these get shorter.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

from tests.shared.tool_api import TOOL_BORDER

REPO_ROOT = Path(__file__).resolve().parents[2]

#: Internal tool-tier modules each consumer still imports directly.
_ALLOWED: dict[str, frozenset[str]] = {
    "tools": frozenset(
        {
            "core.tool_framework.skill_guidance",
            "core.tool_framework.tags",
            "core.tool_framework.tool_decorator",
        }
    ),
    "integrations": frozenset(
        {
            "core.tool_framework.tags",
            "core.tool_framework.tool_decorator",
        }
    ),
    "gateway": frozenset(),
    "surfaces": frozenset(),
    "infrastructure": frozenset(),
}


@pytest.mark.parametrize("consumer", sorted(_ALLOWED))
def test_consumer_tool_tier_imports_match_the_allowlist(consumer: str) -> None:
    # Arrange / Act
    imported = TOOL_BORDER.internal_imports_under(
        REPO_ROOT / consumer, exclude_parts=frozenset({"tests"})
    )

    # Assert
    TOOL_BORDER.assert_matches_allowlist(imported, _ALLOWED[consumer], consumer=f"{consumer}/")


def test_bootstrap_reaches_the_tool_tier_only_through_its_api() -> None:
    """The composition root wires tools together; it must not open the tier up."""
    # Arrange / Act
    imported = TOOL_BORDER.internal_imports_under(REPO_ROOT / "bootstrap")

    # Assert
    assert imported == set()


def test_the_contract_door_survives_being_imported_second() -> None:
    """``import core.llm.types`` must not re-enter a half-built ``core.tool``.

    ``core.tool.execution`` needs ``ToolCall`` from ``core.llm.types``, which in
    turn names ``RuntimeTool`` as a PEP 695 bound. Once ``core/tool/__init__``
    imports execution, a runtime import of that bound makes importing
    ``core.llm.types`` first fail with ``cannot import name 'ToolCall' from
    partially initialized module``. The bound is lazy, so the import stays under
    ``TYPE_CHECKING`` — and this test is what says so out loud.
    """
    # Arrange: a fresh interpreter, importing the risky order first.
    script = "import core.llm.types, core.tool; print(len(core.tool.__all__))"

    # Act
    proc = subprocess.run(
        [sys.executable, "-c", script],
        cwd=str(REPO_ROOT),
        capture_output=True,
        text=True,
        check=False,
    )

    # Assert
    assert proc.returncode == 0, proc.stderr.strip()[-400:]
    assert proc.stdout.strip() == "16"
