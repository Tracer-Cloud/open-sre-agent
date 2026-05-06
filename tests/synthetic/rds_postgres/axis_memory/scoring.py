"""Memory-anchoring scoring annotation for the synthetic RDS suite.

Annotates each axis pair with a memory-mode classification:

    memory_helped   memory primed from `base` made `sibling` converge correctly
                    on a case where the unprimed (baseline) run got it wrong.
    memory_hurt     memory primed from `base` caused `sibling` to mis-converge
                    on a case where the unprimed (baseline) run got it right.
    memory_neutral  the primed run and the baseline run produce the same
                    pass/fail outcome on `sibling`.
    pre_existing    the baseline run already mis-converges on `sibling`,
                    independent of memory.
    not_run         memory mode not exercised yet (Contextual Memory feature
                    not implemented; only the baseline mode is shippable today).

The classifier reads two `ScenarioScore` objects produced by the existing
`run_suite.score_result(...)` machinery. It does not re-implement scoring; it
compares pass/fail outcomes between baseline and memory-primed runs of the
same `sibling` scenario.

Usage:

    from tests.synthetic.rds_postgres.axis_memory.scoring import (
        MemoryMode,
        annotate_pair,
    )

    annotation = annotate_pair(
        base_scenario_id="002-connection-exhaustion",
        sibling_scenario_id="007-connection-pressure-noisy-healthy",
        baseline_score=baseline_sibling_score,
        memory_score=memory_sibling_score,  # may be None until #1234 ships
    )

    # annotation.memory_mode is one of MemoryMode values
    # annotation.baseline_passed / memory_passed are bools or None
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Any


class MemoryMode(StrEnum):
    """Classification of whether memory injection helped, hurt, or no-op'd a run."""

    MEMORY_HELPED = "memory_helped"
    MEMORY_HURT = "memory_hurt"
    MEMORY_NEUTRAL = "memory_neutral"
    PRE_EXISTING = "pre_existing"
    NOT_RUN = "not_run"


@dataclass(frozen=True)
class PairAnnotation:
    """Annotation produced by `annotate_pair` for a single axis pair."""

    pair_id: str
    base_scenario_id: str
    sibling_scenario_id: str
    baseline_passed: bool | None
    memory_passed: bool | None
    memory_mode: MemoryMode
    notes: str = ""


def annotate_pair(
    pair_id: str,
    base_scenario_id: str,
    sibling_scenario_id: str,
    baseline_score: Any | None,
    memory_score: Any | None = None,
) -> PairAnnotation:
    """Classify a pair's memory-anchoring outcome.

    `baseline_score` is the result of running `sibling` with no memory primed.
    `memory_score` is the result of running `sibling` with memory primed from
    `base`. When `memory_score` is None (Contextual Memory not yet wired up),
    the annotation is recorded as NOT_RUN and `baseline_passed` is still
    captured so the suite can report on baseline correctness alone.

    Both score arguments are duck-typed against `run_suite.ScenarioScore`:
    they only need a `.passed` attribute.
    """
    baseline_passed = _passed(baseline_score)
    memory_passed = _passed(memory_score)

    if memory_passed is None:
        return PairAnnotation(
            pair_id=pair_id,
            base_scenario_id=base_scenario_id,
            sibling_scenario_id=sibling_scenario_id,
            baseline_passed=baseline_passed,
            memory_passed=None,
            memory_mode=MemoryMode.NOT_RUN,
            notes="memory mode not exercised; baseline only",
        )

    if baseline_passed is None:
        # Memory mode ran but baseline didn't — refuse to classify.
        return PairAnnotation(
            pair_id=pair_id,
            base_scenario_id=base_scenario_id,
            sibling_scenario_id=sibling_scenario_id,
            baseline_passed=None,
            memory_passed=memory_passed,
            memory_mode=MemoryMode.NOT_RUN,
            notes="memory ran but baseline missing; cannot classify",
        )

    if not baseline_passed and not memory_passed:
        return PairAnnotation(
            pair_id=pair_id,
            base_scenario_id=base_scenario_id,
            sibling_scenario_id=sibling_scenario_id,
            baseline_passed=False,
            memory_passed=False,
            memory_mode=MemoryMode.PRE_EXISTING,
            notes="sibling fails without memory too; not a memory-attributable failure",
        )

    if baseline_passed and not memory_passed:
        return PairAnnotation(
            pair_id=pair_id,
            base_scenario_id=base_scenario_id,
            sibling_scenario_id=sibling_scenario_id,
            baseline_passed=True,
            memory_passed=False,
            memory_mode=MemoryMode.MEMORY_HURT,
            notes="memory caused regression on sibling that passes without memory",
        )

    if not baseline_passed and memory_passed:
        return PairAnnotation(
            pair_id=pair_id,
            base_scenario_id=base_scenario_id,
            sibling_scenario_id=sibling_scenario_id,
            baseline_passed=False,
            memory_passed=True,
            memory_mode=MemoryMode.MEMORY_HELPED,
            notes="memory recovered a baseline failure; uncommon, worth investigating",
        )

    # both passed
    return PairAnnotation(
        pair_id=pair_id,
        base_scenario_id=base_scenario_id,
        sibling_scenario_id=sibling_scenario_id,
        baseline_passed=True,
        memory_passed=True,
        memory_mode=MemoryMode.MEMORY_NEUTRAL,
        notes="memory injection did not change pass/fail; safe for this pair",
    )


def _passed(score: Any | None) -> bool | None:
    """Return `score.passed` if score is truthy, else None.

    Accepts duck-typed scores so this module does not import the full
    `ScenarioScore` dataclass and remains import-light at module load time.
    """
    if score is None:
        return None
    passed_attr = getattr(score, "passed", None)
    if passed_attr is None:
        return None
    return bool(passed_attr)


def summarize_annotations(annotations: list[PairAnnotation]) -> dict[str, int]:
    """Tally annotations by memory_mode for headline reporting."""
    tally: dict[str, int] = {mode.value: 0 for mode in MemoryMode}
    for annotation in annotations:
        tally[annotation.memory_mode.value] += 1
    return tally
