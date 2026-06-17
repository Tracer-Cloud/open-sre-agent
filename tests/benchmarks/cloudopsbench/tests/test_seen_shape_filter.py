"""Tests for the shape filter on ``CloudOpsBenchAdapter.load_cases``.

Pin the contract for the four ways operators set ``filters.seen_shape``:

  [SHAPE_SEEN, SHAPE_UNSEEN]  -> all cases (mid-shape included)
  [SHAPE_SEEN]                -> only startup + runtime
  [SHAPE_UNSEEN]              -> only admission + performance
  []                          -> all cases (no filter)

The first case is the regression guard. A previous implementation
checked ``tag in {True, False}``, which excluded ``None``-tagged
(SHAPE_MID) cases and silently dropped 99/452 of the corpus.
"""

from __future__ import annotations

import pytest

from tests.benchmarks._framework.types import CaseFilters
from tests.benchmarks.cloudopsbench.adapter import CloudOpsBenchAdapter
from tests.benchmarks.cloudopsbench.case_loader import BENCHMARK_DIR
from tests.benchmarks.cloudopsbench.tags import SHAPE_SEEN, SHAPE_UNSEEN

pytestmark = [
    pytest.mark.cloudopsbench,
    pytest.mark.skipif(
        not BENCHMARK_DIR.is_dir(),
        reason="CloudOpsBench benchmark data is not downloaded; run "
        "`make download-cloudopsbench-hf` first.",
    ),
]


# --------------------------------------------------------------------------- #
# Helpers                                                                     #
# --------------------------------------------------------------------------- #


def _load(seen_shape: list[bool] | None) -> tuple[int, set[str]]:
    """Run the adapter's load_cases and return (count, distinct fault categories)."""
    adapter = CloudOpsBenchAdapter()
    filters = CaseFilters(seen_shape=seen_shape if seen_shape is not None else [], seed=42)
    cases = list(adapter.load_cases(filters))
    categories = {(c.metadata.get("fault_category") or "?").lower() for c in cases}
    return len(cases), categories


# --------------------------------------------------------------------------- #
# Tests                                                                       #
# --------------------------------------------------------------------------- #


def test_both_labels_returns_all_categories() -> None:
    """Asking for both labels means "give me everything."

    Before the fix this dropped every mid-shape case quietly. After the
    fix it returns all seven fault categories the corpus has."""
    _, categories = _load([SHAPE_SEEN, SHAPE_UNSEEN])
    # Every shape bucket must appear at least once. The presence of
    # mid-shape categories is the structural proof that the bug is fixed:
    # the prior implementation excluded every None-tagged case.
    seen_buckets = {"startup", "runtime"}
    unseen_buckets = {"admission", "performance"}
    mid_buckets = {"scheduling", "service", "service_routing", "infrastructure", "infra"}
    assert categories & seen_buckets, f"seen categories missing: {categories}"
    assert categories & unseen_buckets, f"unseen categories missing: {categories}"
    assert categories & mid_buckets, (
        f"mid-shape categories were dropped — that is the bug we just fixed: {categories}"
    )


def test_only_seen_returns_only_seen_categories() -> None:
    """``seen_shape=[SHAPE_SEEN]`` means "only the easy categories."

    Mid-shape and unseen-shape cases must not appear in the result."""
    _, categories = _load([SHAPE_SEEN])
    assert categories <= {"startup", "runtime"}, (
        f"seen-only filter let other categories through: {categories}"
    )
    assert "startup" in categories
    assert "runtime" in categories


def test_only_unseen_returns_only_unseen_categories() -> None:
    """``seen_shape=[SHAPE_UNSEEN]`` means "only the hard categories."

    Mid-shape and seen-shape cases must not appear in the result."""
    _, categories = _load([SHAPE_UNSEEN])
    assert categories <= {"admission", "performance"}, (
        f"unseen-only filter let other categories through: {categories}"
    )
    assert "admission" in categories
    assert "performance" in categories


def test_empty_filter_returns_all_categories() -> None:
    """No filter set means "no filter applied" — return every category.

    This is what happens when a config does not list ``seen_shape`` at all."""
    _, categories = _load([])
    # Same answer as asking for both labels: every bucket present.
    assert categories & {"startup", "runtime"}
    assert categories & {"admission", "performance"}
    assert categories & {"scheduling", "service", "service_routing", "infrastructure", "infra"}
