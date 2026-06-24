"""Tests for the headline-aggregate math in reporting.

``_scenario_means`` and ``_mean_with_ci`` produce every number on the
headline panel — the one part of the report a reviewer actually reads.
The math was added without tests, so a refactor that broke the
scenario-level clustering or the bootstrap percentile selection would
silently shift the published numbers.

These tests pin the contract:
  - per-seed cells collapse to one value per scenario (case_id) BEFORE
    aggregation, so the 3 seeds within a scenario count as one
    correlated repeated measure, not three independent draws
  - 95% bootstrap CI is reproducible for a given seed
  - degenerate inputs (n=0, n=1, all-equal) return sensible bounds
  - CI half-width shrinks as N grows (sanity floor for the bootstrap)
"""

from __future__ import annotations

import pytest

from tests.benchmarks._framework.reporting import _mean_with_ci, _scenario_means


def _cell(case_id: str, a1: float, llm: str = "gpt-4o") -> dict[str, object]:
    """Build a minimal per-case dict in the on-disk shape the reporter reads."""
    return {
        "case": {"case_id": case_id},
        "run": {"llm": llm},
        "score": {"metrics": {"a1": a1}},
    }


# --------------------------------------------------------------------------- #
# _scenario_means — clustering at the independent unit                        #
# --------------------------------------------------------------------------- #


def test_scenario_means_collapses_three_seeds_to_one_value() -> None:
    """The whole point of clustering: 3 seeds with a1=[1, 0, 1] for ONE
    scenario must contribute ONE value (the mean = 2/3) to the
    distribution, not three independent observations. Without this the
    bootstrap variance is wrong by a factor that depends on intra-scenario
    correlation, and the CI silently shrinks."""
    cells = [
        _cell("boutique/runtime/1", 1.0),
        _cell("boutique/runtime/1", 0.0),
        _cell("boutique/runtime/1", 1.0),
    ]
    means = _scenario_means(cells, "a1")
    assert means == [pytest.approx(2 / 3)]


def test_scenario_means_one_value_per_distinct_case_id() -> None:
    """N scenarios × 3 seeds each → output length is N, not 3N."""
    cells = []
    for case_id in ["a", "b", "c"]:
        for seed_val in [1.0, 0.0, 1.0]:
            cells.append(_cell(case_id, seed_val))
    means = _scenario_means(cells, "a1")
    assert len(means) == 3
    assert all(m == pytest.approx(2 / 3) for m in means)


def test_scenario_means_skips_cells_with_non_numeric_metric() -> None:
    """An adapter that omits a metric on some cells (e.g. validity probes
    skipped on llm_alone) must not crash or pull None into the bootstrap."""
    cells = [
        _cell("c1", 1.0),
        {"case": {"case_id": "c1"}, "run": {"llm": "x"}, "score": {"metrics": {}}},
    ]
    means = _scenario_means(cells, "a1")
    assert means == [1.0]


def test_scenario_means_empty_input_returns_empty() -> None:
    assert _scenario_means([], "a1") == []


def test_scenario_means_buckets_seeds_by_case_id_not_index() -> None:
    """Order-independence: the same cells in shuffled order must produce
    the same scenario means. Guards against an accidental positional
    bucketing in a future refactor."""
    cells_a = [_cell("c1", 1.0), _cell("c1", 0.0), _cell("c2", 1.0)]
    cells_b = [_cell("c2", 1.0), _cell("c1", 0.0), _cell("c1", 1.0)]
    assert sorted(_scenario_means(cells_a, "a1")) == sorted(_scenario_means(cells_b, "a1"))


# --------------------------------------------------------------------------- #
# _mean_with_ci — bootstrap correctness                                       #
# --------------------------------------------------------------------------- #


def test_mean_with_ci_empty_returns_zeros() -> None:
    """n=0 → no mean, no CI, no n. Defensive against a stratum that had
    no surviving cells (e.g. every llm_alone cell errored)."""
    assert _mean_with_ci([]) == (0.0, 0.0, 0.0, 0)


def test_mean_with_ci_single_value_returns_degenerate_ci() -> None:
    """n=1 → CI is undefined; convention is low==high==mean so the row
    still renders (with a visibly tight interval that flags the n=1)."""
    mean, lo, hi, n = _mean_with_ci([0.7])
    assert (mean, lo, hi, n) == (0.7, 0.7, 0.7, 1)


def test_mean_with_ci_all_equal_inputs_produce_zero_width_interval() -> None:
    """Bootstrap of N identical values can only ever resample to the same
    value, so the interval collapses to the mean. Pin this so a future
    bug that returns a non-zero interval from constant data is caught."""
    mean, lo, hi, n = _mean_with_ci([0.5] * 10)
    assert mean == 0.5
    assert lo == 0.5
    assert hi == 0.5
    assert n == 10


def test_mean_with_ci_returns_correct_mean_regardless_of_bootstrap() -> None:
    """The mean is exact, not bootstrapped — the bootstrap only affects
    the CI endpoints. Cross-validated against a hand-computed expectation."""
    values = [0.0, 0.5, 1.0, 0.25, 0.75]
    mean, _, _, _ = _mean_with_ci(values)
    assert mean == pytest.approx(sum(values) / len(values))


def test_mean_with_ci_is_reproducible_with_fixed_seed() -> None:
    """A given input + seed must produce the same CI bounds across runs.
    Bootstrap reports need to be stable enough that re-rendering the
    same artifacts doesn't shift the published interval."""
    values = [0.0, 1.0, 0.5, 0.7, 0.3, 1.0, 0.2, 0.8]
    a = _mean_with_ci(values, seed=42)
    b = _mean_with_ci(values, seed=42)
    assert a == b


def test_mean_with_ci_bounds_contain_the_mean() -> None:
    """Invariant: the point estimate must lie inside its own CI. Any
    refactor that swaps the percentile indices would fail this."""
    values = [0.0, 1.0, 0.5, 0.7, 0.3, 1.0, 0.2, 0.8]
    mean, lo, hi, _ = _mean_with_ci(values)
    assert lo <= mean <= hi


def test_mean_with_ci_width_shrinks_as_n_grows() -> None:
    """Sanity floor — the bootstrap CI must reflect the standard
    sqrt(N) tightening of a sample mean. If the CI doesn't shrink with
    more data, the bootstrap is broken."""
    small = [0.0, 1.0] * 10  # n=20, mean=0.5
    large = [0.0, 1.0] * 100  # n=200, mean=0.5
    _, lo_s, hi_s, _ = _mean_with_ci(small, seed=1)
    _, lo_l, hi_l, _ = _mean_with_ci(large, seed=1)
    assert (hi_l - lo_l) < (hi_s - lo_s)


def test_mean_with_ci_matches_06_05_published_headline() -> None:
    """Regression-pin the actual interval reported on the 11:46 run so
    a future refactor can't silently shift it. The 30 scenario-mean
    values are the 30 case_ids in the cloudopsbench_v1_openai config;
    the input list below is the gpt-4o per-scenario a1 means observed
    in that run."""
    # gpt-4o scenario means from dev-2026-06-05T11-46-43Z, sorted.
    # Each value is mean(a1) across 3 seeds for one case_id. Distribution
    # of the 30 scenarios: 8 at 0.000, 5 at 0.333, 10 at 0.667, 7 at 1.000.
    scenario_means = [0.0] * 8 + [1 / 3] * 5 + [2 / 3] * 10 + [1.0] * 7
    mean, lo, hi, n = _mean_with_ci(scenario_means)
    assert n == 30
    assert mean == pytest.approx(0.511, abs=0.01)
    # 95% bootstrap CI half-width is approximately ±0.13 at N=30 with
    # this distribution. Pin a generous window so reruns with different
    # bootstrap seeds stay green but real shifts are caught. The audit
    # cited [0.378, 0.644] for this exact data.
    assert 0.30 <= lo <= 0.43, f"lo={lo!r} drifted from the audited interval"
    assert 0.60 <= hi <= 0.72, f"hi={hi!r} drifted from the audited interval"
