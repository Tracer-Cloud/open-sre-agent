"""Static tests locking sibling tool disambiguation in descriptions."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, cast

import pytest

from integrations.s3.tools.s3_get_object_tool import get_s3_object
from integrations.s3.tools.s3_inspect_tool import inspect_s3_object
from integrations.s3.tools.s3_list_tool import list_s3_objects
from integrations.s3.tools.s3_marker_tool import check_s3_marker

FIXTURE_PATH = Path(__file__).parent.parent / "fixtures" / "sibling_pairs.json"

TOOL_REGISTRY: dict[str, Any] = {
    "get_s3_object": get_s3_object.__opensre_registered_tool__,  # type: ignore[attr-defined]
    "inspect_s3_object": inspect_s3_object.__opensre_registered_tool__,  # type: ignore[attr-defined]
    "list_s3_objects": list_s3_objects.__opensre_registered_tool__,  # type: ignore[attr-defined]
    "check_s3_marker": check_s3_marker.__opensre_registered_tool__,  # type: ignore[attr-defined]
}


def _load_sibling_pairs() -> list[dict[str, Any]]:
    with open(FIXTURE_PATH, encoding="utf-8") as f:
        data = json.load(f)
    pairs = data.get("pairs", [])
    return cast(list[dict[str, Any]], pairs)


@pytest.fixture
def sibling_pairs() -> list[dict[str, Any]]:
    return _load_sibling_pairs()


def test_sibling_pairs_fixture_validity(sibling_pairs: list[dict[str, Any]]) -> None:
    """Ensure fixture records valid non-empty sibling pairs."""
    assert len(sibling_pairs) > 0
    for pair in sibling_pairs:
        assert pair["tool_a"] in TOOL_REGISTRY
        assert pair["tool_b"] in TOOL_REGISTRY
        assert pair["tool_a"] != pair["tool_b"]
        assert len(pair["required_in_a"]) > 0
        assert len(pair["required_in_b"]) > 0


@pytest.mark.parametrize(
    "pair_data",
    _load_sibling_pairs(),
    ids=lambda p: f"{p['tool_a']}_vs_{p['tool_b']}",
)
def test_sibling_tool_disambiguation(pair_data: dict[str, Any]) -> None:
    """Assert that confusable sibling tool descriptions explicitly cross-reference each other."""
    tool_a_name = pair_data["tool_a"]
    tool_b_name = pair_data["tool_b"]

    tool_a = TOOL_REGISTRY[tool_a_name]
    tool_b = TOOL_REGISTRY[tool_b_name]

    desc_a = tool_a.description
    desc_b = tool_b.description

    for required_phrase in pair_data["required_in_a"]:
        assert required_phrase.lower() in desc_a.lower(), (
            f"Tool '{tool_a_name}' description must contain '{required_phrase}' to disambiguate "
            f"against sibling '{tool_b_name}'. Actual description: {desc_a}"
        )

    for required_phrase in pair_data["required_in_b"]:
        assert required_phrase.lower() in desc_b.lower(), (
            f"Tool '{tool_b_name}' description must contain '{required_phrase}' to disambiguate "
            f"against sibling '{tool_a_name}'. Actual description: {desc_b}"
        )
