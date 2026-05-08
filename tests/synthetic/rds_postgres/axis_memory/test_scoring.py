"""Direct unit tests for `axis_memory.scoring.annotate_pair`.

The classifier is small and pure: it takes two pass/fail signals and emits
one of five `MemoryMode` values. Tests cover every cell of the truth table
plus the duck-typing behaviour around None and the absent `passed` attribute.
"""

from __future__ import annotations

from dataclasses import dataclass

import pytest

from tests.synthetic.rds_postgres.axis_memory.scoring import (
    MemoryMode,
    PairAnnotation,
    annotate_pair,
    summarize_annotations,
)

pytestmark = pytest.mark.synthetic

# ---------------------------------------------------------------------------
# Test fixtures
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class _FakeScore:
    """Minimal stand-in for `run_suite.ScenarioScore` — only `passed` is read."""

    passed: bool


def _passed() -> _FakeScore:
    return _FakeScore(passed=True)


def _failed() -> _FakeScore:
    return _FakeScore(passed=False)


# ---------------------------------------------------------------------------
# Truth table — every memory_mode classification
# ---------------------------------------------------------------------------


def test_memory_neutral_when_both_pass() -> None:
    annotation = annotate_pair(
        pair_id="p",
        base_scenario_id="A",
        sibling_scenario_id="B",
        baseline_score=_passed(),
        memory_score=_passed(),
    )
    assert annotation.memory_mode is MemoryMode.MEMORY_NEUTRAL
    assert annotation.baseline_passed is True
    assert annotation.memory_passed is True


def test_memory_hurt_when_baseline_passes_but_memory_fails() -> None:
    annotation = annotate_pair(
        pair_id="p",
        base_scenario_id="A",
        sibling_scenario_id="B",
        baseline_score=_passed(),
        memory_score=_failed(),
    )
    assert annotation.memory_mode is MemoryMode.MEMORY_HURT
    assert annotation.baseline_passed is True
    assert annotation.memory_passed is False


def test_memory_helped_when_baseline_fails_but_memory_passes() -> None:
    annotation = annotate_pair(
        pair_id="p",
        base_scenario_id="A",
        sibling_scenario_id="B",
        baseline_score=_failed(),
        memory_score=_passed(),
    )
    assert annotation.memory_mode is MemoryMode.MEMORY_HELPED
    assert annotation.baseline_passed is False
    assert annotation.memory_passed is True


def test_pre_existing_when_both_fail() -> None:
    annotation = annotate_pair(
        pair_id="p",
        base_scenario_id="A",
        sibling_scenario_id="B",
        baseline_score=_failed(),
        memory_score=_failed(),
    )
    assert annotation.memory_mode is MemoryMode.PRE_EXISTING
    assert annotation.baseline_passed is False
    assert annotation.memory_passed is False


def test_not_run_when_memory_score_missing() -> None:
    annotation = annotate_pair(
        pair_id="p",
        base_scenario_id="A",
        sibling_scenario_id="B",
        baseline_score=_passed(),
        memory_score=None,
    )
    assert annotation.memory_mode is MemoryMode.NOT_RUN
    assert annotation.baseline_passed is True
    assert annotation.memory_passed is None


def test_not_run_when_baseline_missing_but_memory_present() -> None:
    annotation = annotate_pair(
        pair_id="p",
        base_scenario_id="A",
        sibling_scenario_id="B",
        baseline_score=None,
        memory_score=_passed(),
    )
    assert annotation.memory_mode is MemoryMode.NOT_RUN
    assert annotation.baseline_passed is None
    assert annotation.memory_passed is True


# ---------------------------------------------------------------------------
# Duck-typing edge cases
# ---------------------------------------------------------------------------


def test_score_without_passed_attribute_is_treated_as_none() -> None:
    """`_passed` returns None when the score has no `.passed`. The classifier
    treats that the same as score=None — i.e. records NOT_RUN."""

    class _OpaqueScore:
        pass

    annotation = annotate_pair(
        pair_id="p",
        base_scenario_id="A",
        sibling_scenario_id="B",
        baseline_score=_OpaqueScore(),
        memory_score=_passed(),
    )
    assert annotation.memory_mode is MemoryMode.NOT_RUN


@pytest.mark.parametrize(
    ("baseline_truthy", "memory_truthy", "expected"),
    [
        (1, 1, MemoryMode.MEMORY_NEUTRAL),
        (1, 0, MemoryMode.MEMORY_HURT),
        (0, 1, MemoryMode.MEMORY_HELPED),
        (0, 0, MemoryMode.PRE_EXISTING),
    ],
)
def test_passed_is_coerced_to_bool(
    baseline_truthy: int, memory_truthy: int, expected: MemoryMode
) -> None:
    """Truthy / falsy `passed` attribute values are coerced via bool()."""
    annotation = annotate_pair(
        pair_id="p",
        base_scenario_id="A",
        sibling_scenario_id="B",
        baseline_score=_FakeScore(passed=bool(baseline_truthy)),
        memory_score=_FakeScore(passed=bool(memory_truthy)),
    )
    assert annotation.memory_mode is expected


# ---------------------------------------------------------------------------
# summarize_annotations
# ---------------------------------------------------------------------------


def test_summarize_annotations_tallies_by_mode() -> None:
    annotations = [
        PairAnnotation(
            pair_id="p1",
            base_scenario_id="A",
            sibling_scenario_id="B",
            baseline_passed=True,
            memory_passed=True,
            memory_mode=MemoryMode.MEMORY_NEUTRAL,
        ),
        PairAnnotation(
            pair_id="p2",
            base_scenario_id="A",
            sibling_scenario_id="C",
            baseline_passed=True,
            memory_passed=False,
            memory_mode=MemoryMode.MEMORY_HURT,
        ),
        PairAnnotation(
            pair_id="p3",
            base_scenario_id="A",
            sibling_scenario_id="D",
            baseline_passed=True,
            memory_passed=False,
            memory_mode=MemoryMode.MEMORY_HURT,
        ),
    ]
    tally = summarize_annotations(annotations)
    assert tally[MemoryMode.MEMORY_NEUTRAL.value] == 1
    assert tally[MemoryMode.MEMORY_HURT.value] == 2
    assert tally[MemoryMode.MEMORY_HELPED.value] == 0
    assert tally[MemoryMode.PRE_EXISTING.value] == 0
    assert tally[MemoryMode.NOT_RUN.value] == 0


def test_summarize_empty_list_returns_zero_for_every_mode() -> None:
    tally = summarize_annotations([])
    assert all(count == 0 for count in tally.values())
    # Every MemoryMode key is present even when the input is empty.
    assert set(tally.keys()) == {mode.value for mode in MemoryMode}
