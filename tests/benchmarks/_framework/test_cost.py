"""Unit tests for the cost-accounting + budget-enforcement module."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor

import pytest

from tests.benchmarks._framework.cost import (
    PRICING_TABLE,
    CostBudgetExceeded,
    CostTracker,
    ModelUsage,
    RunSizeEstimate,
    TokenPricing,
    UnknownModel,
    compute_run_cost,
    estimate_run_cost,
    lookup_pricing,
    register_pricing,
)

# --------------------------------------------------------------------------- #
# TokenPricing                                                                #
# --------------------------------------------------------------------------- #


def test_token_pricing_computes_per_million_usd() -> None:
    pricing = TokenPricing(input_usd_per_mtok=3.0, output_usd_per_mtok=15.0)
    # 1M input + 500k output = $3 + $7.50 = $10.50
    assert pricing.cost_for(1_000_000, 500_000) == pytest.approx(10.50)


def test_token_pricing_zero_tokens_is_zero_cost() -> None:
    assert TokenPricing(3.0, 15.0).cost_for(0, 0) == 0.0


# --------------------------------------------------------------------------- #
# Lookup + registration                                                       #
# --------------------------------------------------------------------------- #


def test_lookup_pricing_known_model_returns_entry() -> None:
    pricing = lookup_pricing("claude-sonnet-4-5-20250929")
    assert pricing.input_usd_per_mtok == 3.0
    assert pricing.output_usd_per_mtok == 15.0


def test_lookup_pricing_unknown_model_raises_unknown_model() -> None:
    with pytest.raises(UnknownModel) as exc_info:
        lookup_pricing("definitely-not-a-real-model-xyz")
    assert "definitely-not-a-real-model-xyz" in str(exc_info.value)


def test_unknown_model_carries_model_id() -> None:
    err = UnknownModel("test-model-9000")
    assert err.model == "test-model-9000"


def test_register_pricing_adds_or_overrides_entry() -> None:
    original = PRICING_TABLE.get("test-temp-model")
    try:
        register_pricing("test-temp-model", TokenPricing(0.5, 1.5))
        assert lookup_pricing("test-temp-model").input_usd_per_mtok == 0.5
        # Override
        register_pricing("test-temp-model", TokenPricing(2.0, 4.0))
        assert lookup_pricing("test-temp-model").input_usd_per_mtok == 2.0
    finally:
        if original is None:
            PRICING_TABLE.pop("test-temp-model", None)
        else:
            PRICING_TABLE["test-temp-model"] = original


def test_compute_run_cost_round_trip() -> None:
    expected = 100_000 / 1_000_000.0 * 3.0 + 50_000 / 1_000_000.0 * 15.0
    actual = compute_run_cost("claude-sonnet-4-5-20250929", 100_000, 50_000)
    assert actual == pytest.approx(expected)


# --------------------------------------------------------------------------- #
# CostTracker                                                                 #
# --------------------------------------------------------------------------- #


def test_cost_tracker_rejects_non_positive_budget() -> None:
    with pytest.raises(ValueError):
        CostTracker(budget_usd=0)
    with pytest.raises(ValueError):
        CostTracker(budget_usd=-1.0)


def test_cost_tracker_starts_empty() -> None:
    tracker = CostTracker(budget_usd=10.0)
    assert tracker.total_cost_usd() == 0.0
    assert tracker.remaining_usd() == 10.0
    assert tracker.by_model() == {}


def test_cost_tracker_add_accumulates_per_model() -> None:
    tracker = CostTracker(budget_usd=100.0)
    tracker.add("claude-sonnet-4-5-20250929", 100_000, 50_000)
    tracker.add("claude-sonnet-4-5-20250929", 50_000, 25_000)
    tracker.add("gpt-4o-2024-11-20", 200_000, 100_000)

    by_model = tracker.by_model()
    assert set(by_model.keys()) == {"claude-sonnet-4-5-20250929", "gpt-4o-2024-11-20"}
    claude = by_model["claude-sonnet-4-5-20250929"]
    assert claude.tokens_in == 150_000
    assert claude.tokens_out == 75_000
    assert claude.call_count == 2
    gpt = by_model["gpt-4o-2024-11-20"]
    assert gpt.call_count == 1


def test_cost_tracker_add_returns_call_cost_in_usd() -> None:
    tracker = CostTracker(budget_usd=10.0)
    # claude-sonnet pricing: $3 in / $15 out per mtok
    # 100k in + 50k out → 0.3 + 0.75 = 1.05
    call_cost = tracker.add("claude-sonnet-4-5-20250929", 100_000, 50_000)
    assert call_cost == pytest.approx(1.05)


def test_cost_tracker_rejects_negative_tokens() -> None:
    tracker = CostTracker(budget_usd=10.0)
    with pytest.raises(ValueError):
        tracker.add("claude-sonnet-4-5-20250929", -1, 0)
    with pytest.raises(ValueError):
        tracker.add("claude-sonnet-4-5-20250929", 0, -1)


def test_cost_tracker_unknown_model_raises_unknown_model() -> None:
    tracker = CostTracker(budget_usd=10.0)
    with pytest.raises(UnknownModel):
        tracker.add("not-a-real-model-xyz", 100, 100)


def test_cost_tracker_budget_exceeded_raises_before_recording() -> None:
    """CostBudgetExceeded must fire BEFORE the over-budget call is recorded,
    so the totals reflect only successfully-recorded calls."""
    tracker = CostTracker(budget_usd=1.0)
    # First call fits: 100k in + 50k out at sonnet ≈ $1.05 → already over.
    # Use a cheaper model: gpt-4o-mini is $0.15/$0.60.
    # 1M in + 1M out = $0.75 (under $1)
    tracker.add("gpt-4o-mini-2024-07-18", 1_000_000, 1_000_000)
    assert tracker.total_cost_usd() == pytest.approx(0.75)

    # Next call would push us over: 1M in + 1M out again would be $1.50 total.
    with pytest.raises(CostBudgetExceeded) as exc_info:
        tracker.add("gpt-4o-mini-2024-07-18", 1_000_000, 1_000_000)
    err = exc_info.value
    assert err.current_usd == pytest.approx(0.75)
    assert err.budget_usd == 1.0
    assert err.would_add_usd == pytest.approx(0.75)

    # Tracker state unchanged after the rejection
    assert tracker.total_cost_usd() == pytest.approx(0.75)
    assert tracker.by_model()["gpt-4o-mini-2024-07-18"].call_count == 1


def test_cost_tracker_summary_round_trip() -> None:
    tracker = CostTracker(budget_usd=100.0)
    tracker.add("claude-sonnet-4-5-20250929", 1_000_000, 1_000_000)
    summary = tracker.summary()
    assert summary["budget_usd"] == 100.0
    assert summary["total_calls"] == 1
    assert summary["total_tokens_in"] == 1_000_000
    assert summary["total_tokens_out"] == 1_000_000
    by_model = summary["by_model"]
    assert isinstance(by_model, dict)
    assert "claude-sonnet-4-5-20250929" in by_model


def test_cost_tracker_by_model_snapshot_is_a_copy() -> None:
    """by_model() returns a snapshot — mutating it must not affect the tracker."""
    tracker = CostTracker(budget_usd=10.0)
    tracker.add("claude-sonnet-4-5-20250929", 100, 100)
    snapshot = tracker.by_model()
    snapshot["claude-sonnet-4-5-20250929"].tokens_in = 999_999
    # Tracker's internal state must be unchanged
    assert tracker.by_model()["claude-sonnet-4-5-20250929"].tokens_in == 100


def test_cost_tracker_is_thread_safe_under_parallel_add() -> None:
    """All add() calls accumulate correctly under concurrent execution."""
    tracker = CostTracker(budget_usd=1000.0)
    n_calls = 200

    def add_one(_i: int) -> None:
        tracker.add("gpt-4o-mini-2024-07-18", 1000, 1000)

    with ThreadPoolExecutor(max_workers=16) as pool:
        list(pool.map(add_one, range(n_calls)))

    by_model = tracker.by_model()
    assert by_model["gpt-4o-mini-2024-07-18"].call_count == n_calls
    assert by_model["gpt-4o-mini-2024-07-18"].tokens_in == n_calls * 1000
    assert by_model["gpt-4o-mini-2024-07-18"].tokens_out == n_calls * 1000


# --------------------------------------------------------------------------- #
# Pre-flight estimator                                                        #
# --------------------------------------------------------------------------- #


def test_estimate_run_cost_assumes_worst_case_per_model() -> None:
    size = RunSizeEstimate(
        estimated_tokens_in_per_run=10_000,
        estimated_tokens_out_per_run=5_000,
        cell_count=10,
        runs_per_cell=2,
    )
    estimate = estimate_run_cost(
        models=["claude-sonnet-4-5-20250929", "gpt-4o-2024-11-20"],
        size=size,
    )
    # 10 cells × 2 runs = 20 runs per model
    # claude: (10k×3 + 5k×15) / 1M = 0.105/run → 2.10 total
    # gpt-4o: (10k×2.5 + 5k×10) / 1M = 0.075/run → 1.50 total
    assert estimate.per_model_upper_bound_usd["claude-sonnet-4-5-20250929"] == pytest.approx(2.10)
    assert estimate.per_model_upper_bound_usd["gpt-4o-2024-11-20"] == pytest.approx(1.50)
    assert estimate.upper_bound_usd == pytest.approx(3.60)


def test_run_size_estimate_total_runs() -> None:
    size = RunSizeEstimate(
        estimated_tokens_in_per_run=1,
        estimated_tokens_out_per_run=1,
        cell_count=7,
        runs_per_cell=3,
    )
    assert size.total_runs == 21


def test_model_usage_default_is_zero() -> None:
    usage = ModelUsage()
    assert usage.tokens_in == 0
    assert usage.tokens_out == 0
    assert usage.cost_usd == 0.0
    assert usage.call_count == 0
