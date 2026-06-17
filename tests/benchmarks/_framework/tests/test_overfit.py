"""Unit tests for the overfit attribution guards.

Each guard is tested independently with hand-crafted baseline/variant
cell pairs that exercise the pass/fail boundary. The aggregator + CLI
are smoke-tested end-to-end.
"""

from __future__ import annotations

from typing import Any

import pytest

from tests.benchmarks._framework.overfit import (
    a_a_consistency,
    aggregate_lift,
    analyze,
    flipped_loss_to_win_clusters,
    held_out_generalization_gate,
    held_out_split,
    per_stratum_uniformity,
    per_system_uniformity,
)

# ─────────────────────────────────────────────────────────────────────────────
# Helpers — build fake cell dicts matching the framework's emitted shape
# ─────────────────────────────────────────────────────────────────────────────


def _make_cell(
    case_id: str,
    *,
    system: str,
    fault_category: str,
    a1: float,
    mode: str = "opensre+llm",
    run_index: int = 0,
    gt_fault_object: str = "app/example",
) -> dict[str, Any]:
    """Build the minimum cell dict the guards read from. Mirrors the framework's
    emitted shape only on the fields the guards touch — keeps tests focused."""
    return {
        "case": {
            "case_id": case_id,
            "metadata": {
                "system": system,
                "fault_category": fault_category,
                "ground_truth": {"fault_object": gt_fault_object},
            },
        },
        "run": {"mode": mode, "run_index": run_index},
        "score": {"metrics": {"a1": a1}},
    }


def _scenario(
    case_id: str,
    *,
    system: str = "boutique",
    fault_category: str = "runtime",
    baseline_a1: float,
    variant_a1: float,
    gt: str = "app/example",
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Build a baseline + variant cell pair for one scenario (one run each)."""
    return (
        [
            _make_cell(
                case_id,
                system=system,
                fault_category=fault_category,
                a1=baseline_a1,
                gt_fault_object=gt,
            )
        ],
        [
            _make_cell(
                case_id,
                system=system,
                fault_category=fault_category,
                a1=variant_a1,
                gt_fault_object=gt,
            )
        ],
    )


def _merge(*pairs: tuple[list[Any], list[Any]]) -> tuple[list[Any], list[Any]]:
    base: list[Any] = []
    var: list[Any] = []
    for b, v in pairs:
        base.extend(b)
        var.extend(v)
    return base, var


# ─────────────────────────────────────────────────────────────────────────────
# aggregate_lift — paired per-scenario delta
# ─────────────────────────────────────────────────────────────────────────────


def test_aggregate_lift_basic() -> None:
    baseline, variant = _merge(
        _scenario("s1", baseline_a1=0.0, variant_a1=1.0),
        _scenario("s2", baseline_a1=1.0, variant_a1=1.0),
    )
    lift, n = aggregate_lift(baseline, variant, "opensre+llm")
    assert n == 2
    assert lift == 0.5  # (0.5+1.0)/2 vs (1.0+1.0)/2 — paired wins on s1, ties on s2


def test_aggregate_lift_returns_zero_on_empty_intersection() -> None:
    baseline = [_make_cell("s1", system="boutique", fault_category="runtime", a1=0.0)]
    variant = [_make_cell("s2", system="boutique", fault_category="runtime", a1=1.0)]
    lift, n = aggregate_lift(baseline, variant, "opensre+llm")
    assert lift == 0.0
    assert n == 0


# ─────────────────────────────────────────────────────────────────────────────
# Guard A — per-system uniformity
# ─────────────────────────────────────────────────────────────────────────────


def test_per_system_uniformity_passes_when_lifts_match() -> None:
    baseline, variant = _merge(
        _scenario("b1", system="boutique", baseline_a1=0.0, variant_a1=1.0),
        _scenario("t1", system="trainticket", baseline_a1=0.0, variant_a1=1.0),
    )
    verdict = per_system_uniformity(baseline, variant, "opensre+llm")
    assert verdict.passed
    assert verdict.measurement == 0.0


def test_per_system_uniformity_fails_when_lift_is_concentrated() -> None:
    baseline, variant = _merge(
        _scenario("b1", system="boutique", baseline_a1=0.0, variant_a1=0.0),
        _scenario("t1", system="trainticket", baseline_a1=0.0, variant_a1=1.0),
    )
    verdict = per_system_uniformity(baseline, variant, "opensre+llm")
    assert not verdict.passed
    assert verdict.measurement == 1.0  # trainticket +1.0, boutique 0.0 → spread = 1.0


# ─────────────────────────────────────────────────────────────────────────────
# Guard B — per-stratum uniformity
# ─────────────────────────────────────────────────────────────────────────────


def test_per_stratum_uniformity_passes_when_balanced() -> None:
    baseline, variant = _merge(
        _scenario("c1", fault_category="runtime", baseline_a1=0.0, variant_a1=0.5),
        _scenario("c2", fault_category="admission", baseline_a1=0.0, variant_a1=0.5),
    )
    verdict = per_stratum_uniformity(baseline, variant, "opensre+llm")
    assert verdict.passed


def test_per_stratum_uniformity_fails_when_one_category_dominates() -> None:
    # Use 3 strata so median is well-defined as the middle value;
    # runtime carries all the lift while the other two are flat.
    baseline, variant = _merge(
        _scenario("r1", fault_category="runtime", baseline_a1=0.0, variant_a1=1.0),
        _scenario("a1", fault_category="admission", baseline_a1=0.0, variant_a1=0.1),
        _scenario("p1", fault_category="performance", baseline_a1=0.0, variant_a1=0.1),
    )
    verdict = per_stratum_uniformity(baseline, variant, "opensre+llm")
    assert not verdict.passed
    # Float division of 1.0 / 0.1: exact 10.0 on CPython 3.12, but IEEE 754
    # makes the result implementation-defined across versions/platforms.
    # ``pytest.approx`` shields the test from that drift.
    assert verdict.measurement == pytest.approx(10.0)


def test_per_stratum_uniformity_handles_no_positive_lifts() -> None:
    baseline, variant = _merge(
        _scenario("s1", baseline_a1=1.0, variant_a1=0.0),
    )
    verdict = per_stratum_uniformity(baseline, variant, "opensre+llm")
    assert verdict.passed
    assert verdict.measurement is None


def test_per_stratum_uniformity_fails_when_single_stratum_carries_lift() -> None:
    """Regression for the single-stratum blind spot: when all positive lift
    is concentrated in one of multiple strata, max/median of a one-element
    list is 1.0 — so the ratio check silently passed despite being THE
    textbook overfit pattern. The explicit single-positive-stratum branch
    catches it as ``measurement=inf``."""
    baseline, variant = _merge(
        _scenario("r1", fault_category="runtime", baseline_a1=0.0, variant_a1=1.0),
        _scenario("a1", fault_category="admission", baseline_a1=0.0, variant_a1=0.0),
        _scenario("p1", fault_category="performance", baseline_a1=0.0, variant_a1=0.0),
        _scenario("st1", fault_category="startup", baseline_a1=0.0, variant_a1=0.0),
    )
    verdict = per_stratum_uniformity(baseline, variant, "opensre+llm")
    assert not verdict.passed
    assert verdict.measurement == float("inf")
    assert "single stratum" in verdict.detail["reason"]


def test_per_stratum_uniformity_fails_when_single_positive_others_negative() -> None:
    """Same single-stratum failure mode, but with neighboring strata actively
    regressing — the variant lifts one category while hurting others. Also
    overfit (plus regression), should fail."""
    baseline, variant = _merge(
        _scenario("r1", fault_category="runtime", baseline_a1=0.0, variant_a1=1.0),
        _scenario("a1", fault_category="admission", baseline_a1=0.5, variant_a1=0.0),
        _scenario("p1", fault_category="performance", baseline_a1=0.5, variant_a1=0.0),
    )
    verdict = per_stratum_uniformity(baseline, variant, "opensre+llm")
    assert not verdict.passed
    assert verdict.measurement == float("inf")


# ─────────────────────────────────────────────────────────────────────────────
# Guard C — cluster concentration
# ─────────────────────────────────────────────────────────────────────────────


def test_cluster_concentration_passes_when_flips_spread() -> None:
    baseline, variant = _merge(
        _scenario(
            "c1",
            system="boutique",
            fault_category="runtime",
            baseline_a1=0.0,
            variant_a1=1.0,
            gt="app/checkoutservice",
        ),
        _scenario(
            "c2",
            system="trainticket",
            fault_category="admission",
            baseline_a1=0.0,
            variant_a1=1.0,
            gt="app/ts-payment-service",
        ),
        _scenario(
            "c3",
            system="boutique",
            fault_category="startup",
            baseline_a1=0.0,
            variant_a1=1.0,
            gt="app/cartservice",
        ),
    )
    verdict = flipped_loss_to_win_clusters(baseline, variant, "opensre+llm")
    # 3 flips, 3 distinct clusters → max concentration = 1/3 < 0.60
    assert verdict.passed


def test_cluster_concentration_fails_when_one_cluster_dominates() -> None:
    baseline, variant = _merge(
        _scenario(
            "p1",
            system="trainticket",
            fault_category="runtime",
            baseline_a1=0.0,
            variant_a1=1.0,
            gt="app/ts-payment-alpha",
        ),
        _scenario(
            "p2",
            system="trainticket",
            fault_category="runtime",
            baseline_a1=0.0,
            variant_a1=1.0,
            gt="app/ts-payment-beta",
        ),
        _scenario(
            "p3",
            system="trainticket",
            fault_category="runtime",
            baseline_a1=0.0,
            variant_a1=1.0,
            gt="app/ts-payment-gamma",
        ),
        _scenario(
            "p4",
            system="boutique",
            fault_category="runtime",
            baseline_a1=0.0,
            variant_a1=1.0,
            gt="app/checkoutservice",
        ),
    )
    verdict = flipped_loss_to_win_clusters(baseline, variant, "opensre+llm")
    # The 3 ts-payment-* flips cluster via the GT-prefix logic → 3/4 = 75% > 60%
    assert not verdict.passed
    assert verdict.measurement == 0.75


def test_cluster_concentration_counts_multi_replicate_scenarios_correctly() -> None:
    """Regression for the run_index collapse bug.

    The framework emits one cell per (case, mode, run_index), but
    ``run_index`` isn't in the cell dict — it's only encoded in the
    filename. The prior implementation keyed cells by
    ``(case_id, run.get("run_index", 0))`` so all 3 replicates of a case
    landed under the same key and only the last replicate survived. With
    runs_per_case=3, Guard C operated on ≤ 1/3 of the intended flips.

    Build a case with 3 baseline replicates (all a1=0) and 3 variant
    replicates (all a1=1) for each of two distinct cases. The fixed
    implementation should count 2 flips; the buggy implementation would
    have counted 2 only by coincidence (last-replicate wins), but would
    miss flips whose last replicate wasn't a clear-cut rescue.
    """
    base_cells = []
    var_cells = []
    for case_id, system, cat, gt in [
        ("c1", "boutique", "runtime", "app/checkoutservice"),
        ("c2", "trainticket", "admission", "app/ts-payment-service"),
    ]:
        for _ in range(3):
            base_cells.append(
                _make_cell(case_id, system=system, fault_category=cat, a1=0.0, gt_fault_object=gt)
            )
            var_cells.append(
                _make_cell(case_id, system=system, fault_category=cat, a1=1.0, gt_fault_object=gt)
            )
    verdict = flipped_loss_to_win_clusters(base_cells, var_cells, "opensre+llm")
    assert verdict.passed
    assert verdict.detail["total_flips"] == 2  # two scenarios flipped, not 6 cells


def test_cluster_concentration_treats_partial_replicate_rescue_as_flip() -> None:
    """Scenario-level semantics: variant majority-win counts as a flip
    even if not every variant replicate scored. Baseline must be all-fail
    (every replicate a1=0) AND variant must be majority-win (mean ≥ 0.5)."""
    base_cells = []
    var_cells = []
    # case c1: 3 baseline lose, 2 of 3 variant win → flip
    for _ in range(3):
        base_cells.append(_make_cell("c1", system="boutique", fault_category="runtime", a1=0.0))
    var_cells.append(_make_cell("c1", system="boutique", fault_category="runtime", a1=1.0))
    var_cells.append(_make_cell("c1", system="boutique", fault_category="runtime", a1=1.0))
    var_cells.append(_make_cell("c1", system="boutique", fault_category="runtime", a1=0.0))
    # case c2: 3 baseline lose, 1 of 3 variant wins (minority) → NOT a flip
    for _ in range(3):
        base_cells.append(_make_cell("c2", system="boutique", fault_category="runtime", a1=0.0))
    var_cells.append(_make_cell("c2", system="boutique", fault_category="runtime", a1=1.0))
    var_cells.append(_make_cell("c2", system="boutique", fault_category="runtime", a1=0.0))
    var_cells.append(_make_cell("c2", system="boutique", fault_category="runtime", a1=0.0))

    verdict = flipped_loss_to_win_clusters(base_cells, var_cells, "opensre+llm")
    # Only c1 should be counted (majority-win); c2 is a minority blip.
    assert verdict.detail["total_flips"] == 1


def test_cluster_concentration_handles_no_flips() -> None:
    baseline, variant = _merge(
        _scenario("s1", baseline_a1=1.0, variant_a1=1.0),
    )
    verdict = flipped_loss_to_win_clusters(baseline, variant, "opensre+llm")
    assert verdict.passed
    assert verdict.measurement is None


# ─────────────────────────────────────────────────────────────────────────────
# Guard D — held-out generalization
# ─────────────────────────────────────────────────────────────────────────────


def test_held_out_split_is_reproducible() -> None:
    case_ids = [f"c{i}" for i in range(100)]
    split_a = held_out_split(case_ids, seed=42)
    split_b = held_out_split(case_ids, seed=42)
    assert split_a == split_b
    assert len(split_a) == 20  # 20% of 100


def test_held_out_split_differs_by_seed() -> None:
    case_ids = [f"c{i}" for i in range(100)]
    assert held_out_split(case_ids, seed=42) != held_out_split(case_ids, seed=43)


def test_held_out_generalization_ships_when_lifts_match() -> None:
    baseline_pairs: list[tuple[list[Any], list[Any]]] = []
    for i in range(100):
        baseline_pairs.append(_scenario(f"c{i}", baseline_a1=0.0, variant_a1=0.5))
    baseline, variant = _merge(*baseline_pairs)
    verdict = held_out_generalization_gate(baseline, variant, "opensre+llm")
    # Both optimize and held-out get the same +0.5 lift → ratio = 1.0
    assert verdict.passed
    assert verdict.detail["zone"] == "ship"


def test_held_out_generalization_rejects_when_held_out_collapses() -> None:
    case_ids = [f"c{i}" for i in range(100)]
    held_set = held_out_split(case_ids, seed=42)
    baseline_pairs: list[tuple[list[Any], list[Any]]] = []
    for cid in case_ids:
        if cid in held_set:
            # No lift on held-out
            baseline_pairs.append(_scenario(cid, baseline_a1=0.5, variant_a1=0.5))
        else:
            # Big lift on optimize
            baseline_pairs.append(_scenario(cid, baseline_a1=0.0, variant_a1=1.0))
    baseline, variant = _merge(*baseline_pairs)
    verdict = held_out_generalization_gate(baseline, variant, "opensre+llm")
    assert not verdict.passed
    assert verdict.detail["zone"] == "reject"
    assert verdict.measurement == 0.0


def test_held_out_generalization_handles_no_optimize_lift() -> None:
    baseline, variant = _merge(
        _scenario("c1", baseline_a1=1.0, variant_a1=1.0),
    )
    verdict = held_out_generalization_gate(baseline, variant, "opensre+llm")
    # No positive optimize lift = no overfit signal to detect
    assert verdict.passed


# ─────────────────────────────────────────────────────────────────────────────
# Guard E — A/A consistency
# ─────────────────────────────────────────────────────────────────────────────


def _variant_cells(*case_a1: tuple[str, float]) -> list[dict[str, Any]]:
    """Build a list of variant cells for the A/A tests — same shape as the
    framework emits but constructed directly to skip the (baseline, variant)
    pair abstraction the other helpers use."""
    return [
        _make_cell(case_id, system="boutique", fault_category="runtime", a1=a1)
        for case_id, a1 in case_a1
    ]


def test_a_a_consistency_passes_when_two_seeds_agree() -> None:
    """Two A/A runs with identical aggregate A@1 → diff = 0 → pass."""
    seed_a = _variant_cells(("c1", 0.5), ("c2", 0.5))
    seed_b = _variant_cells(("c1", 0.5), ("c2", 0.5))
    verdict = a_a_consistency(seed_a, seed_b, "opensre+llm")
    assert verdict.passed
    assert verdict.measurement == 0.0


def test_a_a_consistency_fails_when_two_seeds_diverge() -> None:
    """Two A/A runs differing by 0.3 in aggregate → diff > 0.02 → fail."""
    seed_a = _variant_cells(("c1", 0.8), ("c2", 0.8))
    seed_b = _variant_cells(("c1", 0.5), ("c2", 0.5))
    verdict = a_a_consistency(seed_a, seed_b, "opensre+llm")
    assert not verdict.passed
    assert verdict.measurement is not None
    assert abs(verdict.measurement - 0.3) < 1e-9


def test_a_a_consistency_fails_when_no_overlapping_cases() -> None:
    """If the two A/A runs share zero case_ids, the noise floor cannot be
    bounded and the guard must fail (not silently pass on an empty paired set)."""
    seed_a = _variant_cells(("c1", 0.5))
    seed_b = _variant_cells(("c2", 0.5))
    verdict = a_a_consistency(seed_a, seed_b, "opensre+llm")
    assert not verdict.passed
    assert "no overlapping case_ids" in verdict.detail["reason"]


# ─────────────────────────────────────────────────────────────────────────────
# analyze() aggregator
# ─────────────────────────────────────────────────────────────────────────────


def test_analyze_without_a_a_variant_marks_a_a_not_evaluated_and_blocks_ship() -> None:
    """The pre-reg requires the A/A guard. Omitting the second variant run
    must NOT let a report ship — the A/A guard appears as not-evaluated
    and ``ship`` returns False even when the four other guards pass."""
    pairs = []
    systems = ["boutique", "trainticket"]
    categories = ["runtime", "admission", "performance", "startup"]
    for i in range(40):
        pairs.append(
            _scenario(
                f"c{i}",
                system=systems[i % 2],
                fault_category=categories[i % 4],
                baseline_a1=0.5,
                variant_a1=0.75,
                gt=f"app/service-{i}",
            )
        )
    baseline, variant = _merge(*pairs)
    report = analyze(baseline, variant)  # no a_a_variant
    assert not report.ship
    a_a = next(g for g in report.guards if g.name == "a_a_consistency")
    assert not a_a.passed
    assert "not evaluated" in a_a.detail["reason"]


def test_analyze_returns_ship_true_when_all_guards_pass() -> None:
    """A real-mechanism win: uniform lift, balanced flips, held-out generalizes,
    AND the A/A consistency pair agrees within the noise floor."""
    pairs = []
    a_a_pairs = []
    systems = ["boutique", "trainticket"]
    categories = ["runtime", "admission", "performance", "startup"]
    for i in range(40):
        sys = systems[i % 2]
        cat = categories[i % 4]
        pairs.append(
            _scenario(
                f"c{i}",
                system=sys,
                fault_category=cat,
                baseline_a1=0.5,
                variant_a1=0.75,
                gt=f"app/service-{i}",  # distinct per-case prefixes prevent cluster concentration
            )
        )
        # A/A pair: second variant run with identical per-case A@1 so the
        # noise-floor diff is exactly 0 — passes the A/A guard.
        a_a_pairs.append(
            _scenario(
                f"c{i}",
                system=sys,
                fault_category=cat,
                baseline_a1=0.5,
                variant_a1=0.75,
                gt=f"app/service-{i}",
            )
        )
    baseline, variant = _merge(*pairs)
    _, a_a_variant = _merge(*a_a_pairs)
    report = analyze(baseline, variant, a_a_variant=a_a_variant)
    assert report.ship


def test_analyze_returns_ship_false_when_held_out_collapses() -> None:
    """Concentrated lift on optimize, flat on held-out → overfit signal → no ship.

    Passes a deterministic A/A variant (identical to ``variant``) so the A/A
    guard succeeds trivially and ship=False is unambiguously attributable
    to the held-out collapse, not A/A non-evaluation.
    """
    case_ids = [f"c{i}" for i in range(100)]
    held = held_out_split(case_ids, seed=42)
    pairs = []
    for i, cid in enumerate(case_ids):
        sys = "boutique" if i % 2 == 0 else "trainticket"
        cat = ["runtime", "admission", "performance", "startup"][i % 4]
        if cid in held:
            pairs.append(
                _scenario(
                    cid,
                    system=sys,
                    fault_category=cat,
                    baseline_a1=0.5,
                    variant_a1=0.5,
                    gt=f"app/service-{i}",
                )
            )
        else:
            pairs.append(
                _scenario(
                    cid,
                    system=sys,
                    fault_category=cat,
                    baseline_a1=0.0,
                    variant_a1=1.0,
                    gt=f"app/service-{i}",
                )
            )
    baseline, variant = _merge(*pairs)
    report = analyze(baseline, variant, a_a_variant=variant)
    assert not report.ship
    held_out_verdict = next(g for g in report.guards if g.name == "held_out_generalization")
    assert not held_out_verdict.passed
    a_a_verdict = next(g for g in report.guards if g.name == "a_a_consistency")
    assert a_a_verdict.passed  # A/A trivially passed; held-out alone tanked ship
