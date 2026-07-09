"""Tests for canonical investigation loop analytics metrics."""

from __future__ import annotations

from config.constants.investigation import MAX_INVESTIGATION_LOOPS
from platform.analytics.investigation_loop import (
    investigation_iteration_cap_from_state,
    investigation_loop_count_from_state,
    loop_metrics_from_state,
    loop_properties,
    merge_loop_properties,
)


def test_loop_properties_from_state() -> None:
    count, cap = loop_metrics_from_state(
        {
            "investigation_loop_count": 7,
            "investigation_iteration_cap": 20,
        }
    )
    assert count == 7
    assert cap == 20
    assert loop_properties(loop_count=count, iteration_cap=cap) == {
        "investigation_loop_count": 7,
        "investigation_iteration_cap": 20,
    }


def test_loop_count_defaults_to_zero_without_state() -> None:
    assert investigation_loop_count_from_state(None) == 0
    assert investigation_iteration_cap_from_state(None) == MAX_INVESTIGATION_LOOPS


def test_merge_loop_properties_preserves_existing_fields() -> None:
    merged = merge_loop_properties(
        {"investigation_id": "inv-1", "status": "completed"},
        loop_count=3,
        iteration_cap=20,
    )
    assert merged["investigation_id"] == "inv-1"
    assert merged["investigation_loop_count"] == 3
    assert merged["investigation_iteration_cap"] == 20
