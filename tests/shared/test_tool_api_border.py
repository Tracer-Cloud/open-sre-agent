"""Consumers import the tool tier through its API, and the surface only shrinks.

``core/tool/`` (contract, execution, registry port) and ``core/tool_framework/``
(``@tool``, skill guidance, payload utilities) are one tier to everything above
them. ``core.tool_framework.utils`` is already a door — it curates ``__all__``
over nine helper submodules — so an import of it is not internal. The package
roots export nothing yet; everything reaching past them is listed below, and
these allowlists are the measurement of the seam.

Each allowlist is compared exactly in both directions: a new internal import
fails immediately, and an entry no longer imported must be removed. Widening a
door and moving callers onto it is how these get shorter.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from tests.shared.tool_api import TOOL_BORDER

REPO_ROOT = Path(__file__).resolve().parents[2]

#: Internal tool-tier modules each consumer still imports directly.
_ALLOWED: dict[str, frozenset[str]] = {
    "tools": frozenset(
        {
            "core.tool.contracts",
            "core.tool.execution",
            "core.tool.registry",
            "core.tool_framework.skill_guidance",
            "core.tool_framework.tags",
            "core.tool_framework.tool_decorator",
        }
    ),
    "integrations": frozenset(
        {
            "core.tool.contracts",
            "core.tool.execution",
            "core.tool_framework.tags",
            "core.tool_framework.tool_decorator",
        }
    ),
    "gateway": frozenset({"core.tool.execution"}),
    "surfaces": frozenset(
        {
            "core.tool.contracts",
            "core.tool.execution",
        }
    ),
    "platform": frozenset(
        {
            "core.tool.contracts",
            "core.tool.registry",
        }
    ),
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
