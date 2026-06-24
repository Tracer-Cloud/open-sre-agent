"""Tests for CloudOpsBenchAdapter.select_best_run — majority-vote selector.

The selector picks the canonical run from a self-consistency batch by
majority vote on ``final_diagnosis.top_3_predictions[0].fault_taxonomy``.
06-05 run analysis showed this closes 100% of the gpt-5 consistency gap
(median 0.567 → selected 0.667 = paper baseline exactly) and 60% of the
gpt-4o gap, at zero extra LLM-call cost.

Regressions here change the reported A@1 silently — pinned with explicit
votes/ties so future contributors can't accidentally shift the policy.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import pytest

from tests.benchmarks._framework.adapters import CaseScore, RunResult
from tests.benchmarks.cloudopsbench.adapter import CloudOpsBenchAdapter


def _make_run(taxonomy: str, *, a1: float = 0.0) -> tuple[RunResult, CaseScore]:
    """Build a (RunResult, CaseScore) tuple with one top-3 prediction.

    Empty ``taxonomy`` simulates a predictor-failed run (no prediction at all)
    — the selector should treat those as no-vote.
    """
    top: list[dict[str, Any]] = []
    if taxonomy:
        top.append({"fault_taxonomy": taxonomy, "fault_object": "", "root_cause": ""})
    run = RunResult(
        case_id="c1",
        mode="opensre+llm",
        llm="gpt-4o",
        model_version="(test)",
        opensre_sha="(test)",
        started_at="2026-01-01T00:00:00+00:00",
        ended_at="2026-01-01T00:00:01+00:00",
        ok=True,
        error=None,
        final_diagnosis={"top_3_predictions": top},
        evidence_entries=[],
        tokens_in=0,
        tokens_out=0,
        cost_usd=0.0,
        latency_ms=1000,
    )
    score = CaseScore(case_id="c1", metrics={"a1": a1})
    return run, score


@dataclass
class _StubCase:
    """Adapter call-signature contract only — selector doesn't read the case."""

    case_id: str = "c1"


@pytest.fixture
def adapter() -> CloudOpsBenchAdapter:
    """Adapter with no corpus access — only ``select_best_run`` is exercised."""
    # The constructor scans benchmark_dir for cases; pointing at /tmp gives
    # us an empty adapter that still has the method we want to test.
    return CloudOpsBenchAdapter.__new__(CloudOpsBenchAdapter)


# --------------------------------------------------------------------------- #
# Vote-count behavior                                                          #
# --------------------------------------------------------------------------- #


def test_unanimous_pick_returns_index_zero(adapter: CloudOpsBenchAdapter) -> None:
    """All 3 runs agree on the same taxonomy → return the earliest index.
    Index 0 is deterministic and reproducible across re-runs."""
    runs = [_make_run("Runtime_Fault"), _make_run("Runtime_Fault"), _make_run("Runtime_Fault")]
    assert adapter.select_best_run(_StubCase(), runs) == 0  # type: ignore[arg-type]


def test_majority_2_of_3_picks_earliest_agreeing_run(adapter: CloudOpsBenchAdapter) -> None:
    """2/3 agree; selector picks the FIRST run that produced the winning
    taxonomy. Critical for reproducibility — re-running the bench must
    yield the same pick given the same predictions."""
    runs = [
        _make_run("Startup_Fault"),  # idx 0, will lose
        _make_run("Runtime_Fault"),  # idx 1, winning taxonomy first appearance
        _make_run("Runtime_Fault"),  # idx 2, would also be winning but we pick 1
    ]
    assert adapter.select_best_run(_StubCase(), runs) == 1  # type: ignore[arg-type]


def test_all_different_taxonomies_picks_first_appearance(adapter: CloudOpsBenchAdapter) -> None:
    """When every taxonomy is distinct, each has 1 vote → tiebreak by
    first-appearance order = index 0."""
    runs = [_make_run("A"), _make_run("B"), _make_run("C")]
    assert adapter.select_best_run(_StubCase(), runs) == 0  # type: ignore[arg-type]


def test_blank_predictions_are_ignored_in_vote_tally(adapter: CloudOpsBenchAdapter) -> None:
    """Predictor failures (empty taxonomy) must not vote — otherwise an
    all-failed-but-one scenario could give the lone failure 1 vote and
    no winner can emerge. The single successful prediction wins."""
    runs = [
        _make_run(""),  # predictor failed
        _make_run(""),  # predictor failed
        _make_run("Runtime_Fault"),  # the only real prediction
    ]
    assert adapter.select_best_run(_StubCase(), runs) == 2  # type: ignore[arg-type]


def test_all_predictions_blank_returns_none(adapter: CloudOpsBenchAdapter) -> None:
    """Nothing to vote on → return ``None`` so the runner skips the
    consistency-selected stratum for this scenario. Falls back to median
    in the standard ``all`` stratum."""
    runs = [_make_run(""), _make_run(""), _make_run("")]
    assert adapter.select_best_run(_StubCase(), runs) is None  # type: ignore[arg-type]


# --------------------------------------------------------------------------- #
# Edge cases                                                                   #
# --------------------------------------------------------------------------- #


def test_single_run_returns_zero(adapter: CloudOpsBenchAdapter) -> None:
    """Degenerate self-consistency (runs_per_case=1) — there's only one
    choice. Return 0 to keep callers from special-casing length."""
    runs = [_make_run("Runtime_Fault")]
    assert adapter.select_best_run(_StubCase(), runs) == 0  # type: ignore[arg-type]


def test_empty_runs_list_returns_none(adapter: CloudOpsBenchAdapter) -> None:
    """Defensive: caller passed an empty group (shouldn't happen via the
    runner aggregator, but the contract is to return None rather than
    crash on indexing)."""
    assert adapter.select_best_run(_StubCase(), []) is None  # type: ignore[arg-type]


def test_missing_top_3_predictions_key_treats_as_blank(
    adapter: CloudOpsBenchAdapter,
) -> None:
    """If ``final_diagnosis`` lacks the ``top_3_predictions`` key entirely
    (e.g. the format_final_answer hook never ran), treat as blank — same
    semantics as an empty list. Two real predictions still win."""
    no_pred_run, no_pred_score = _make_run("")
    no_pred_run = no_pred_run.__class__(
        **{**no_pred_run.__dict__, "final_diagnosis": {}}  # no top_3_predictions key
    )
    runs = [
        (no_pred_run, no_pred_score),
        _make_run("Runtime_Fault"),
        _make_run("Runtime_Fault"),
    ]
    assert adapter.select_best_run(_StubCase(), runs) == 1  # type: ignore[arg-type]


# --------------------------------------------------------------------------- #
# Realistic 06-05 vote distribution (sanity check on observed agreement rates) #
# --------------------------------------------------------------------------- #


def test_06_05_observed_agreement_pattern_yields_winner(
    adapter: CloudOpsBenchAdapter,
) -> None:
    """06-05 analysis: 43% of scenarios had all-3-agree, 47% had 2-of-3,
    10% had all-different. Pin a representative 2-of-3 case so future
    regressions to the tiebreak policy are caught — the analysis
    methodology is documented behavior, not an accident."""
    runs = [
        _make_run("Network_Fault"),  # idx 0, lone vote — would be tiebreak winner if tied
        _make_run("Configuration_Fault"),  # idx 1, winning taxonomy first
        _make_run("Configuration_Fault"),  # idx 2
    ]
    # Configuration_Fault has 2 votes vs Network_Fault's 1 → pick idx 1
    assert adapter.select_best_run(_StubCase(), runs) == 1  # type: ignore[arg-type]
