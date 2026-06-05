"""Tests for runner._aggregate_per_stratum — multi-stratum cell aggregation.

Covers four strata the aggregator emits today:

  - ``all``                              — every cell
  - ``seen-shape`` / ``unseen-shape``    — Phase D tagging
  - ``held-out`` / ``optimize``          — generalization-gate split (M8)

The aggregator is the contract between the per-cell scoring code and the
integrity gate's per-stratum requirement (Mechanism 4). Regressions here
silently invalidate published reports.
"""

from __future__ import annotations

from pathlib import Path

from tests.benchmarks._framework.adapters import (
    BenchmarkCase,
    CaseScore,
    Mode,
)
from tests.benchmarks._framework.runner import RunResult, _aggregate_per_stratum, _CellResult


def _make_cell(
    *,
    case_id: str,
    metric_value: float,
    seen_shape: bool | None = None,
    is_held_out: bool | None = None,
    mode: Mode = "opensre+llm",
    llm: str = "claude-4-sonnet",
) -> _CellResult:
    """Construct a minimal _CellResult fixture for aggregation tests."""
    metadata: dict[str, object] = {}
    if is_held_out is not None:
        metadata["is_held_out"] = is_held_out
    case = BenchmarkCase(
        case_id=case_id,
        benchmark_name="test",
        metadata=metadata,
        seen_shape=seen_shape,
    )
    score = CaseScore(case_id=case_id, metrics={"a1": metric_value})
    run = RunResult(
        case_id=case_id,
        mode=mode,
        llm=llm,
        model_version="(test)",
        opensre_sha="(test)",
        started_at="2026-01-01T00:00:00+00:00",
        ended_at="2026-01-01T00:00:01+00:00",
        ok=True,
        error=None,
        final_diagnosis={},
        evidence_entries=[],
        tokens_in=0,
        tokens_out=0,
        cost_usd=0.0,
        latency_ms=1000,
    )
    return _CellResult(
        case=case,
        mode=mode,
        llm=llm,
        run_index=0,
        run=run,
        score=score,
        artifact_path=Path("/dev/null"),
    )


# --------------------------------------------------------------------------- #
# Baseline: empty + all-stratum                                                #
# --------------------------------------------------------------------------- #


def test_aggregate_empty_returns_only_all_stratum() -> None:
    result = _aggregate_per_stratum([], ["a1"])
    assert result == {"all": {}}


def test_aggregate_all_stratum_always_populated() -> None:
    cells = [
        _make_cell(case_id="c1", metric_value=0.4),
        _make_cell(case_id="c2", metric_value=0.6),
    ]
    result = _aggregate_per_stratum(cells, ["a1"])
    # Median of [0.4, 0.6] = 0.5
    assert result["all"]["opensre+llm/claude-4-sonnet"]["a1"] == 0.5


# --------------------------------------------------------------------------- #
# seen-shape / unseen-shape                                                    #
# --------------------------------------------------------------------------- #


def test_seen_shape_true_emits_seen_shape_stratum() -> None:
    cells = [_make_cell(case_id="c1", metric_value=0.8, seen_shape=True)]
    result = _aggregate_per_stratum(cells, ["a1"])
    assert "seen-shape" in result
    assert result["seen-shape"]["opensre+llm/claude-4-sonnet"]["a1"] == 0.8
    assert "unseen-shape" not in result


def test_seen_shape_false_emits_unseen_shape_stratum() -> None:
    cells = [_make_cell(case_id="c1", metric_value=0.4, seen_shape=False)]
    result = _aggregate_per_stratum(cells, ["a1"])
    assert "unseen-shape" in result
    assert result["unseen-shape"]["opensre+llm/claude-4-sonnet"]["a1"] == 0.4
    assert "seen-shape" not in result


def test_seen_shape_none_appears_only_in_all_stratum() -> None:
    """Mid-shape cells (seen_shape=None) must not pollute the seen/unseen
    buckets — they only count toward the 'all' aggregate."""
    cells = [_make_cell(case_id="c1", metric_value=0.5, seen_shape=None)]
    result = _aggregate_per_stratum(cells, ["a1"])
    assert "all" in result
    assert "seen-shape" not in result
    assert "unseen-shape" not in result


# --------------------------------------------------------------------------- #
# held-out / optimize — generalization gate                                    #
# --------------------------------------------------------------------------- #


def test_held_out_true_emits_held_out_stratum() -> None:
    cells = [_make_cell(case_id="c1", metric_value=0.45, is_held_out=True)]
    result = _aggregate_per_stratum(cells, ["a1"])
    assert "held-out" in result
    assert result["held-out"]["opensre+llm/claude-4-sonnet"]["a1"] == 0.45
    assert "optimize" not in result


def test_held_out_false_emits_optimize_stratum() -> None:
    cells = [_make_cell(case_id="c1", metric_value=0.65, is_held_out=False)]
    result = _aggregate_per_stratum(cells, ["a1"])
    assert "optimize" in result
    assert result["optimize"]["opensre+llm/claude-4-sonnet"]["a1"] == 0.65
    assert "held-out" not in result


def test_missing_is_held_out_metadata_appears_only_in_all() -> None:
    """Cells from adapters that haven't tagged is_held_out (e.g., during
    transition) must not silently land in either gen-gate stratum."""
    cells = [_make_cell(case_id="c1", metric_value=0.5, is_held_out=None)]
    result = _aggregate_per_stratum(cells, ["a1"])
    assert "all" in result
    assert "held-out" not in result
    assert "optimize" not in result


# --------------------------------------------------------------------------- #
# Cross-stratum: a cell can land in BOTH seen-shape AND held-out                #
# --------------------------------------------------------------------------- #


def test_cell_tagged_with_both_shape_and_held_out_lands_in_all_strata() -> None:
    """A held-out, seen-shape cell must appear in: all, seen-shape, held-out
    — these are orthogonal axes that report independently."""
    cells = [_make_cell(case_id="c1", metric_value=0.7, seen_shape=True, is_held_out=True)]
    result = _aggregate_per_stratum(cells, ["a1"])
    key = "opensre+llm/claude-4-sonnet"
    assert result["all"][key]["a1"] == 0.7
    assert result["seen-shape"][key]["a1"] == 0.7
    assert result["held-out"][key]["a1"] == 0.7
    assert "unseen-shape" not in result
    assert "optimize" not in result


# --------------------------------------------------------------------------- #
# Multiple cells: median computation per stratum                               #
# --------------------------------------------------------------------------- #


def test_median_computed_per_stratum_independently() -> None:
    """Medians of seen-shape and unseen-shape buckets must NOT pool together."""
    cells = [
        _make_cell(case_id="c1", metric_value=0.9, seen_shape=True),
        _make_cell(case_id="c2", metric_value=0.7, seen_shape=True),
        _make_cell(case_id="c3", metric_value=0.3, seen_shape=False),
        _make_cell(case_id="c4", metric_value=0.1, seen_shape=False),
    ]
    result = _aggregate_per_stratum(cells, ["a1"])
    key = "opensre+llm/claude-4-sonnet"
    # all: median of [0.9, 0.7, 0.3, 0.1] = (0.7 + 0.3) / 2 = 0.5
    assert result["all"][key]["a1"] == 0.5
    # seen-shape: median of [0.9, 0.7] = 0.8
    assert result["seen-shape"][key]["a1"] == 0.8
    # unseen-shape: median of [0.3, 0.1] = 0.2
    assert result["unseen-shape"][key]["a1"] == 0.2


def test_multiple_llms_emit_separate_keys_per_stratum() -> None:
    cells = [
        _make_cell(case_id="c1", metric_value=0.6, llm="claude-4-sonnet"),
        _make_cell(case_id="c2", metric_value=0.7, llm="gpt-4o"),
    ]
    result = _aggregate_per_stratum(cells, ["a1"])
    assert "opensre+llm/claude-4-sonnet" in result["all"]
    assert "opensre+llm/gpt-4o" in result["all"]
    assert result["all"]["opensre+llm/claude-4-sonnet"]["a1"] == 0.6
    assert result["all"]["opensre+llm/gpt-4o"]["a1"] == 0.7


def test_missing_metric_in_cell_score_aggregates_as_zero() -> None:
    """Adapters whose scorer omitted a declared metric should not crash the
    aggregator — the missing value is treated as 0.0 (documented behavior)."""
    cell = _make_cell(case_id="c1", metric_value=0.5)
    # cell.score.metrics has 'a1' but not 'grounding'
    result = _aggregate_per_stratum([cell], ["a1", "grounding"])
    key = "opensre+llm/claude-4-sonnet"
    assert result["all"][key]["a1"] == 0.5
    assert result["all"][key]["grounding"] == 0.0


# --------------------------------------------------------------------------- #
# consistency-selected stratum: emitted when adapter overrides select_best_run #
# --------------------------------------------------------------------------- #


class _ConstSelectorAdapter:
    """Minimal adapter stub for consistency-selection tests.

    Only ``select_best_run`` is exercised; the other ABC methods aren't
    reached by the aggregator. The adapter returns whatever the test
    configures, so each test can pin the selector's behavior explicitly.
    """

    def __init__(self, *, picked: int | None = None, raises: Exception | None = None) -> None:
        self._picked = picked
        self._raises = raises

    def select_best_run(self, _case, _runs):  # noqa: ARG002 — stub signature
        if self._raises is not None:
            raise self._raises
        return self._picked


def test_consistency_stratum_omitted_when_no_adapter_passed() -> None:
    """Existing callers without an adapter keep median-only behavior — the
    new stratum must not appear for them."""
    cell = _make_cell(case_id="c1", metric_value=0.5)
    result = _aggregate_per_stratum([cell], ["a1"])
    assert "consistency-selected" not in result


def test_consistency_stratum_omitted_when_adapter_returns_none() -> None:
    """``None`` from the selector means "no pick, skip this group". With
    every group returning None, the stratum stays out of the report —
    confirms the opt-in shape, no silent empty stratum."""
    cell = _make_cell(case_id="c1", metric_value=0.5)
    result = _aggregate_per_stratum([cell], ["a1"], adapter=_ConstSelectorAdapter(picked=None))
    assert "consistency-selected" not in result


def test_consistency_stratum_uses_picked_cell_metrics_per_scenario() -> None:
    """3 seeds per (case, mode, llm); adapter picks run-index 1. The
    stratum's metric must equal the picked cell's a1, NOT the median of
    all 3 — that's the whole point of selection."""
    cells = [
        _make_cell(case_id="c1", metric_value=0.0),
        _make_cell(case_id="c1", metric_value=1.0),  # adapter picks this one
        _make_cell(case_id="c1", metric_value=0.0),
    ]
    result = _aggregate_per_stratum(cells, ["a1"], adapter=_ConstSelectorAdapter(picked=1))
    key = "opensre+llm/claude-4-sonnet"
    assert result["consistency-selected"][key]["a1"] == 1.0
    # The standard "all" stratum still reports the median across all 3
    # — the new stratum is additive, not a replacement.
    assert result["all"][key]["a1"] == 0.0


def test_consistency_selector_called_per_scenario_not_per_cell() -> None:
    """Two scenarios × 3 seeds each = 6 cells but only 2 selector calls.
    The aggregator must group cells before asking the adapter to pick."""
    call_count = 0

    class _CountingAdapter:
        def select_best_run(self, _case, _runs):  # noqa: ARG002 — interface contract
            nonlocal call_count
            call_count += 1
            return 0

    cells = [
        _make_cell(case_id="c1", metric_value=0.5),
        _make_cell(case_id="c1", metric_value=0.5),
        _make_cell(case_id="c1", metric_value=0.5),
        _make_cell(case_id="c2", metric_value=0.5),
        _make_cell(case_id="c2", metric_value=0.5),
        _make_cell(case_id="c2", metric_value=0.5),
    ]
    _aggregate_per_stratum(cells, ["a1"], adapter=_CountingAdapter())
    assert call_count == 2


def test_consistency_stratum_swallows_selector_exception_with_warning(capsys) -> None:
    """Selector errors must not abort the whole report — fall back to
    median-only. Stderr/stdout warning is the audit signal so the failure
    isn't silent."""
    cell = _make_cell(case_id="c1", metric_value=0.5)
    result = _aggregate_per_stratum(
        [cell], ["a1"], adapter=_ConstSelectorAdapter(raises=RuntimeError("boom"))
    )
    assert "consistency-selected" not in result
    captured = capsys.readouterr()
    assert "select_best_run raised" in captured.out
    assert "boom" in captured.out


def test_consistency_stratum_skips_out_of_bounds_index() -> None:
    """A selector that returns an invalid index (>= len(runs) or < 0) must
    not crash and must not produce a stratum entry for that group."""
    cell = _make_cell(case_id="c1", metric_value=0.5)
    result = _aggregate_per_stratum([cell], ["a1"], adapter=_ConstSelectorAdapter(picked=99))
    assert "consistency-selected" not in result
