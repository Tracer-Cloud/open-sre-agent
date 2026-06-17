"""Overfit attribution — runtime guards for any baseline / variant run pair.

A real-mechanism win lifts uniformly: across both systems in the corpus,
across all four fault categories, and on held-out cases the variant
never saw during development. Overfit looks aggregate-positive but
concentrated — one system, one stratum, one cluster of cases. These
guards detect that concentration before a variant is promoted to default.

Used by every bench experiment as part of the pre-registered decision
matrix; not adapter-specific. Each guard takes the case-level JSONs the
runner emits and returns a structured verdict.

Library-first: public guard functions + an ``analyze`` aggregator are the
primary API. A thin ``main()`` provides CLI access for ad-hoc analysis
against any pair of case directories. Mirrors the layout of
``_framework/integrity.py`` — first-class framework code, not a script.

Public API:
  - ``per_system_uniformity`` — Guard A: boutique vs trainticket lift spread.
  - ``per_stratum_uniformity`` — Guard B: per fault-category lift concentration.
  - ``flipped_loss_to_win_clusters`` — Guard C: which (system, category,
    GT-prefix) clusters absorbed the loss→win flips.
  - ``held_out_generalization_gate`` — Guard D: 80/20 split using the
    seeded protocol; ``held_out_split`` is the underlying utility.
  - ``a_a_consistency`` — Guard E: two-seed same-variant aggregate diff
    bounds the bench's intrinsic noise floor. Requires a second variant
    run (seed differs from the main one). When the second run isn't
    supplied, ``analyze`` returns a "not evaluated" verdict that fails
    the ship check — the A/A run cannot be silently skipped.
  - ``aggregate_lift`` — utility: paired per-scenario A@1 delta between runs.
  - ``analyze`` — runs all five guards (A/A as not-evaluated when its
    second variant run isn't provided), returns ``OverfitReport``.

Constants are aligned to ``exp_structured_outputs_v1.yml`` thresholds and
``cloudopsbench_v1.yml`` held-out seed. Changing either requires updating
the corresponding pre-registration in the same PR.
"""

from __future__ import annotations

import argparse
import json
import random
from collections import Counter, defaultdict
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from statistics import median
from typing import Any

from tests.benchmarks._framework.adapter_base import OverfitDimensions

# ─────────────────────────────────────────────────────────────────────────────
# Constants — mirror the pre-registration. Changing these requires updating
# the matching pre-reg file in the same PR (single source of truth).
# ─────────────────────────────────────────────────────────────────────────────

HELD_OUT_SEED = 42
HELD_OUT_FRAC = 0.20

SHIP_RATIO_THRESHOLD = 0.70  # held_out_lift / optimize_lift ≥ this → ship
REJECT_RATIO_THRESHOLD = 0.30  # < this → reject as overfit
PER_SYSTEM_UNIFORMITY_MAX = 0.05  # boutique vs trainticket lift spread cap
PER_STRATUM_CONCENTRATION_MAX = 2.0  # max-stratum-lift / median-stratum-lift cap
CLUSTER_CONCENTRATION_MAX = 0.60  # any single flip-cluster's share cap
A_A_AGGREGATE_DIFF_MAX = 0.02  # two seeds, same variant: aggregate diff cap


# ─────────────────────────────────────────────────────────────────────────────
# Data shape returned by ``analyze``
# ─────────────────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class GuardVerdict:
    """One guard's measurement + threshold + pass/fail call."""

    name: str
    passed: bool
    measurement: float | None
    threshold: float | None
    detail: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class OverfitReport:
    """Result of running all four guards on a baseline / variant pair."""

    mode: str
    full_corpus_lift: float
    full_corpus_n: int
    guards: list[GuardVerdict]

    @property
    def ship(self) -> bool:
        """True only when every guard passes — a variant that fails any
        single guard does NOT promote, regardless of aggregate lift."""
        return all(g.passed for g in self.guards)


# ─────────────────────────────────────────────────────────────────────────────
# Loading + key helpers
# ─────────────────────────────────────────────────────────────────────────────


def load_cells(case_dir: Path) -> list[dict[str, Any]]:
    """Read every per-case JSON in ``case_dir``.

    Each cell is the framework's standard run-result shape:
    ``{"case": {...}, "run": {...}, "score": {...}}``. The function is
    tolerant of mixed-mode directories — filter by ``cell["run"]["mode"]``
    in the caller.
    """
    cells: list[dict[str, Any]] = []
    for fname in sorted(case_dir.glob("*.json")):
        with open(fname) as f:
            cells.append(json.load(f))
    return cells


def _mean_a1_by_case(cells: list[dict[str, Any]], mode: str) -> dict[str, float]:
    """Mean A@1 per ``case_id`` for ``mode``, averaging across runs.

    The bench's independent unit is the scenario, not the seed — paired
    contrasts and overfit guards both reduce to per-scenario means before
    computing deltas.
    """
    by_case: dict[str, list[float]] = defaultdict(list)
    for cell in cells:
        if cell["run"]["mode"] != mode:
            continue
        by_case[cell["case"]["case_id"]].append(cell["score"]["metrics"]["a1"])
    return {cid: sum(scores) / len(scores) for cid, scores in by_case.items()}


# ─────────────────────────────────────────────────────────────────────────────
# Public utility — paired lift between two runs
# ─────────────────────────────────────────────────────────────────────────────


def aggregate_lift(
    baseline: list[dict[str, Any]],
    variant: list[dict[str, Any]],
    mode: str,
    filter_case_ids: set[str] | None = None,
) -> tuple[float, int]:
    """Mean A@1 lift (variant − baseline) for ``mode``, optionally restricted
    to a ``case_ids`` subset (used by the held-out / optimize split).

    Returns ``(lift, n_paired_scenarios)``. Empty intersection returns
    ``(0.0, 0)`` — caller decides whether that's a meaningful no-data state.
    """
    base_by_case = _mean_a1_by_case(baseline, mode)
    var_by_case = _mean_a1_by_case(variant, mode)
    common = set(base_by_case) & set(var_by_case)
    if filter_case_ids is not None:
        common &= filter_case_ids
    if not common:
        return 0.0, 0
    base_mean = sum(base_by_case[cid] for cid in common) / len(common)
    var_mean = sum(var_by_case[cid] for cid in common) / len(common)
    return var_mean - base_mean, len(common)


def _per_attribute_lift(
    baseline: list[dict[str, Any]],
    variant: list[dict[str, Any]],
    mode: str,
    attribute_fn: Callable[[dict[str, Any]], str],
) -> dict[str, tuple[float, int]]:
    """Lift split by a categorical attribute of each case (system, category)."""
    base_by_case = _mean_a1_by_case(baseline, mode)
    var_by_case = _mean_a1_by_case(variant, mode)
    attr_of_case: dict[str, str] = {
        cell["case"]["case_id"]: attribute_fn(cell) for cell in baseline + variant
    }
    by_attr: dict[str, list[tuple[float, float]]] = defaultdict(list)
    for cid in set(base_by_case) & set(var_by_case):
        by_attr[attr_of_case[cid]].append((base_by_case[cid], var_by_case[cid]))
    return {
        attr: (
            sum(p[1] for p in pairs) / len(pairs) - sum(p[0] for p in pairs) / len(pairs),
            len(pairs),
        )
        for attr, pairs in by_attr.items()
    }


# ─────────────────────────────────────────────────────────────────────────────
# Guard A — per-system uniformity
# ─────────────────────────────────────────────────────────────────────────────


def per_system_uniformity(
    baseline: list[dict[str, Any]],
    variant: list[dict[str, Any]],
    mode: str,
    threshold: float = PER_SYSTEM_UNIFORMITY_MAX,
    dimensions: OverfitDimensions | None = None,
) -> GuardVerdict:
    """A real mechanism lifts both ``boutique`` and ``trainticket`` similarly.

    Spread (max − min) above ``threshold`` indicates the variant learned a
    system-specific pattern instead of a general mechanism.

    ``dimensions`` selects the ``case.metadata`` key used to read each
    case's "system" attribute. Default falls back to CloudOpsBench's
    schema; other adapters pass their own ``OverfitDimensions`` so the
    guard knows which key holds the corpus's system label.
    """
    dims = dimensions or OverfitDimensions()
    per_system = _per_attribute_lift(
        baseline, variant, mode, lambda c: c["case"]["metadata"][dims.system_key]
    )
    if not per_system:
        return GuardVerdict(
            name="per_system_uniformity",
            passed=True,
            measurement=None,
            threshold=threshold,
            detail={"reason": "no paired cells", "per_system": {}},
        )
    lifts = [lift for lift, _ in per_system.values()]
    spread = max(lifts) - min(lifts)
    return GuardVerdict(
        name="per_system_uniformity",
        passed=spread <= threshold,
        measurement=spread,
        threshold=threshold,
        detail={
            "per_system": {sys: {"lift": lift, "n": n} for sys, (lift, n) in per_system.items()},
        },
    )


# ─────────────────────────────────────────────────────────────────────────────
# Guard B — per-stratum uniformity
# ─────────────────────────────────────────────────────────────────────────────


def per_stratum_uniformity(
    baseline: list[dict[str, Any]],
    variant: list[dict[str, Any]],
    mode: str,
    threshold: float = PER_STRATUM_CONCENTRATION_MAX,
    dimensions: OverfitDimensions | None = None,
) -> GuardVerdict:
    """No single fault category should dominate the lift.

    A real mechanism win lifts at least two strata roughly together; lift
    concentrated in a single category is the category-specific overfit
    signature this guard catches. Three branches:

      - 0 positive strata → no lift signal at all (variant ties or
        regresses across the board). Pass; this guard has nothing to say
        about uniformly-bad variants.
      - 1 positive stratum out of >1 total → all lift is in one category.
        Fail with ``measurement=inf`` regardless of magnitude — that IS
        the maximum concentration.
      - 2+ positive strata → ``max(positive lifts) / median(positive lifts)``
        must be ≤ threshold. The ratio measures how disproportionate the
        biggest lift is vs the typical lift.
    """
    dims = dimensions or OverfitDimensions()
    per_stratum = _per_attribute_lift(
        baseline, variant, mode, lambda c: c["case"]["metadata"][dims.stratum_key]
    )
    pos_lifts = [lift for lift, _ in per_stratum.values() if lift > 0]
    per_stratum_detail = {s: {"lift": lift, "n": n} for s, (lift, n) in per_stratum.items()}

    # Branch 1: no positive lifts → variant has no signal to overfit on.
    if not pos_lifts:
        return GuardVerdict(
            name="per_stratum_uniformity",
            passed=True,
            measurement=None,
            threshold=threshold,
            detail={
                "reason": "no positive stratum lifts to assess concentration",
                "per_stratum": per_stratum_detail,
            },
        )

    # Branch 2: exactly one stratum has positive lift while ≥2 exist →
    # concentration is total. Guard against this explicitly because a
    # single-element pos_lifts has max/median = 1.0 and would silently
    # pass the ratio check despite being the textbook overfit pattern.
    if len(pos_lifts) == 1 and len(per_stratum) > 1:
        return GuardVerdict(
            name="per_stratum_uniformity",
            passed=False,
            measurement=float("inf"),
            threshold=threshold,
            detail={
                "reason": (
                    "all positive lift concentrated in a single stratum out of "
                    f"{len(per_stratum)} total — concentration is total"
                ),
                "per_stratum": per_stratum_detail,
            },
        )

    # Branch 3: 2+ positive strata → ratio check.
    med = median(pos_lifts)
    ratio = max(pos_lifts) / med if med > 0 else float("inf")
    return GuardVerdict(
        name="per_stratum_uniformity",
        passed=ratio <= threshold,
        measurement=ratio,
        threshold=threshold,
        detail={"per_stratum": per_stratum_detail},
    )


# ─────────────────────────────────────────────────────────────────────────────
# Guard C — per-case attribution clustering
# ─────────────────────────────────────────────────────────────────────────────


def flipped_loss_to_win_clusters(
    baseline: list[dict[str, Any]],
    variant: list[dict[str, Any]],
    mode: str,
    threshold: float = CLUSTER_CONCENTRATION_MAX,
    dimensions: OverfitDimensions | None = None,
) -> GuardVerdict:
    """Cluster scenarios the variant rescued (baseline all-fail → variant
    majority-win) by ``(system, fault_category, GT-service-prefix)``.

    If a single cluster owns more than ``threshold`` of the flips, that
    cluster IS the variant's overfit fingerprint — it learned a specific
    sub-pattern, not a general lever.

    A "flip" is defined at the **scenario** level (not the per-replicate
    cell): the baseline-mean A@1 across replicates is exactly 0 (every
    replicate lost) AND the variant-mean A@1 is ≥ 0.5 (majority of
    replicates won). Scenario-level semantics avoid the run-index
    matching trap — the framework emits one cell per (case, mode, run)
    but doesn't put ``run_index`` in the cell dict (it's only encoded in
    the filename), so keying by ``(case_id, run.get("run_index", 0))``
    silently collapses all replicates of a case onto the same key. Using
    the mean across replicates is both correct and resilient to that
    schema gap.
    """
    dims = dimensions or OverfitDimensions()
    base_by_case = _mean_a1_by_case(baseline, mode)
    var_by_case = _mean_a1_by_case(variant, mode)
    case_meta = {c["case"]["case_id"]: c["case"]["metadata"] for c in baseline + variant}
    clusters: Counter[tuple[str, str, str]] = Counter()
    for case_id in base_by_case.keys() & var_by_case.keys():
        # All baseline replicates lost AND majority of variant replicates won.
        if base_by_case[case_id] == 0.0 and var_by_case[case_id] >= 0.5:
            meta = case_meta[case_id]
            gt_fo = meta["ground_truth"].get(dims.gt_object_key, "")
            # Prefix-strip the last "-<word>" segment so service families
            # (ts-payment-*, ts-order-*) cluster together rather than each
            # specific service forming its own singleton.
            gt_prefix = gt_fo.rsplit("-", 1)[0] if "-" in gt_fo else gt_fo
            clusters[(meta[dims.system_key], meta[dims.stratum_key], gt_prefix)] += 1
    total_flips = sum(clusters.values())
    if total_flips == 0:
        return GuardVerdict(
            name="cluster_concentration",
            passed=True,
            measurement=None,
            threshold=threshold,
            detail={"reason": "no loss→win flips to cluster", "clusters": {}},
        )
    max_concentration = max(c / total_flips for c in clusters.values())
    top = sorted(clusters.items(), key=lambda kv: -kv[1])[:10]
    # Output dict labels use the adapter's declared dimension key names
    # so the report's vocabulary matches the source data. A
    # ``cluster``-shaped adapter sees ``"cluster": "east"`` instead of
    # ``"system": "east"``. Prior to Phase 3 this was hardcoded as
    # ``"system"`` / ``"fault_category"`` which silently misrepresented
    # any non-CloudOpsBench adapter's report.
    return GuardVerdict(
        name="cluster_concentration",
        passed=max_concentration <= threshold,
        measurement=max_concentration,
        threshold=threshold,
        detail={
            "total_flips": total_flips,
            "top_clusters": [
                {
                    dims.system_key: s,
                    dims.stratum_key: fc,
                    "gt_prefix": gp,
                    "flips": n,
                }
                for (s, fc, gp), n in top
            ],
        },
    )


# ─────────────────────────────────────────────────────────────────────────────
# Guard D — held-out generalization gate
# ─────────────────────────────────────────────────────────────────────────────


def held_out_split(all_case_ids: list[str], seed: int = HELD_OUT_SEED) -> set[str]:
    """Reproducible held-out 20% split — same seeded protocol as the
    pre-registration. The 80/20 boundary is determined by the seeded
    shuffle and case-id stable-sort; identical inputs give identical splits
    across processes."""
    rng = random.Random(seed)
    shuffled = sorted(set(all_case_ids))  # stable order before shuffle for determinism
    rng.shuffle(shuffled)
    n_held_out = int(len(shuffled) * HELD_OUT_FRAC)
    return set(shuffled[:n_held_out])


def held_out_generalization_gate(
    baseline: list[dict[str, Any]],
    variant: list[dict[str, Any]],
    mode: str,
    ship_threshold: float = SHIP_RATIO_THRESHOLD,
    reject_threshold: float = REJECT_RATIO_THRESHOLD,
) -> GuardVerdict:
    """``held_out_lift / optimize_lift`` ratio must clear ``ship_threshold``
    (BDIL Phase F). Below ``reject_threshold`` is flagged overfit.

    The pass/fail call follows the ship threshold — anything below ``ship``
    is a fail (encompasses the reject zone and the warn zone between them).
    The warn vs reject distinction is preserved in ``detail["zone"]`` so
    operators can decide whether to inspect or auto-reject.
    """
    all_case_ids = list({c["case"]["case_id"] for c in baseline + variant})
    held_out = held_out_split(all_case_ids)
    optimize = set(all_case_ids) - held_out
    opt_lift, opt_n = aggregate_lift(baseline, variant, mode, optimize)
    held_lift, held_n = aggregate_lift(baseline, variant, mode, held_out)
    if opt_lift <= 0:
        return GuardVerdict(
            name="held_out_generalization",
            passed=True,
            measurement=None,
            threshold=ship_threshold,
            detail={
                "reason": "no positive optimize lift — no overfit signal to detect",
                "optimize_lift": opt_lift,
                "optimize_n": opt_n,
                "held_out_lift": held_lift,
                "held_out_n": held_n,
            },
        )
    ratio = held_lift / opt_lift
    if ratio >= ship_threshold:
        zone = "ship"
    elif ratio < reject_threshold:
        zone = "reject"
    else:
        zone = "warn"
    return GuardVerdict(
        name="held_out_generalization",
        passed=ratio >= ship_threshold,
        measurement=ratio,
        threshold=ship_threshold,
        detail={
            "zone": zone,
            "optimize_lift": opt_lift,
            "optimize_n": opt_n,
            "held_out_lift": held_lift,
            "held_out_n": held_n,
            "reject_threshold": reject_threshold,
        },
    )


# ─────────────────────────────────────────────────────────────────────────────
# Guard E — A/A consistency (two-seed same-variant)
# ─────────────────────────────────────────────────────────────────────────────


def a_a_consistency(
    variant_seed_a: list[dict[str, Any]],
    variant_seed_b: list[dict[str, Any]],
    mode: str,
    threshold: float = A_A_AGGREGATE_DIFF_MAX,
) -> GuardVerdict:
    """Two runs of the SAME variant with different seeds must agree closely.

    Establishes the bench's intrinsic noise floor. If the two A/A runs'
    aggregate A@1 differ by more than ``threshold``, any "lift" measured
    against a baseline is potentially within sampling noise — the
    mechanism attribution is fragile and the variant cannot be promoted.

    Both runs must be of the SAME variant config; only the seed should
    differ. Mixing variant configs into this call defeats the noise-floor
    semantics — there's no input check for it (call sites are responsible).
    """
    mean_a = _mean_a1_by_case(variant_seed_a, mode)
    mean_b = _mean_a1_by_case(variant_seed_b, mode)
    common = set(mean_a) & set(mean_b)
    if not common:
        return GuardVerdict(
            name="a_a_consistency",
            passed=False,
            measurement=None,
            threshold=threshold,
            detail={
                "reason": (
                    "no overlapping case_ids between the two A/A runs — "
                    "cannot bound the noise floor"
                ),
                "seed_a_n": len(mean_a),
                "seed_b_n": len(mean_b),
            },
        )
    agg_a = sum(mean_a[cid] for cid in common) / len(common)
    agg_b = sum(mean_b[cid] for cid in common) / len(common)
    diff = abs(agg_a - agg_b)
    return GuardVerdict(
        name="a_a_consistency",
        passed=diff <= threshold,
        measurement=diff,
        threshold=threshold,
        detail={
            "seed_a_aggregate_a1": agg_a,
            "seed_b_aggregate_a1": agg_b,
            "paired_n": len(common),
        },
    )


def _a_a_not_evaluated(threshold: float = A_A_AGGREGATE_DIFF_MAX) -> GuardVerdict:
    """The A/A guard verdict when no second variant run was provided.

    Returns ``passed=False`` so ``OverfitReport.ship`` rejects shipping
    until the A/A run lands — the guard cannot be silently skipped. The
    pre-registration locks A/A as a required gate; this reproduces that
    semantics in code so the runtime can't promote without it.
    """
    return GuardVerdict(
        name="a_a_consistency",
        passed=False,
        measurement=None,
        threshold=threshold,
        detail={
            "reason": (
                "A/A consistency was not evaluated — pass a second variant "
                "run (different seed, same config) via ``analyze(..., a_a_variant=...)`` "
                "or the CLI's ``--a-a-variant-dir`` flag. Required by the "
                "pre-registered decision matrix; ship is rejected until it runs."
            ),
        },
    )


# ─────────────────────────────────────────────────────────────────────────────
# Aggregator
# ─────────────────────────────────────────────────────────────────────────────


def analyze(
    baseline: list[dict[str, Any]],
    variant: list[dict[str, Any]],
    mode: str = "opensre+llm",
    a_a_variant: list[dict[str, Any]] | None = None,
    dimensions: OverfitDimensions | None = None,
) -> OverfitReport:
    """Run every guard and aggregate verdicts into a single ``OverfitReport``.

    Callers can introspect each guard's ``GuardVerdict`` or just check
    ``OverfitReport.ship`` for the all-or-nothing decision. Mode defaults
    to ``opensre+llm`` because that's the only arm structural lifts apply
    to in the bench's current schema.

    ``a_a_variant`` is the second variant run (different seed, same config)
    used by Guard E. When omitted, the A/A guard returns a "not evaluated"
    verdict that fails — the report cannot ship without the A/A pair, by
    design. The pre-registered decision matrix locks A/A as required, and
    this aggregator enforces it at the runtime layer.

    ``dimensions`` is forwarded to Guards A, B, and C so the framework
    does not hardcode which ``case.metadata`` keys hold the system /
    stratum / GT-object attributes. ``None`` falls back to
    CloudOpsBench's schema (back-compat); other adapters pass their own
    ``OverfitDimensions`` (typically obtained from
    ``adapter.overfit_dimensions()``).
    """
    full_lift, full_n = aggregate_lift(baseline, variant, mode)
    guards = [
        per_system_uniformity(baseline, variant, mode, dimensions=dimensions),
        per_stratum_uniformity(baseline, variant, mode, dimensions=dimensions),
        flipped_loss_to_win_clusters(baseline, variant, mode, dimensions=dimensions),
        held_out_generalization_gate(baseline, variant, mode),
    ]
    if a_a_variant is None:
        guards.append(_a_a_not_evaluated())
    else:
        guards.append(a_a_consistency(variant, a_a_variant, mode))
    return OverfitReport(
        mode=mode,
        full_corpus_lift=full_lift,
        full_corpus_n=full_n,
        guards=guards,
    )


# ─────────────────────────────────────────────────────────────────────────────
# CLI — thin wrapper around ``analyze``
# ─────────────────────────────────────────────────────────────────────────────


def _format_report(report: OverfitReport) -> str:
    """Human-readable rendering of the report for terminal output."""
    lines: list[str] = []
    lines.append("=" * 78)
    lines.append(f"Overfit attribution — mode={report.mode}")
    lines.append("=" * 78)
    lines.append(f"Full corpus  lift={report.full_corpus_lift:+.3f}  (n={report.full_corpus_n})")
    lines.append("")
    for g in report.guards:
        marker = "PASS" if g.passed else "FAIL"
        meas = f"{g.measurement:.3f}" if g.measurement is not None else "n/a"
        thresh = f"{g.threshold:.3f}" if g.threshold is not None else "n/a"
        lines.append(f"[{marker}] {g.name:<28} measurement={meas}  threshold={thresh}")
        for k, v in g.detail.items():
            if isinstance(v, dict | list) and len(str(v)) > 80:
                lines.append(f"        {k}: {json.dumps(v, default=str)[:200]}...")
            else:
                lines.append(f"        {k}: {v}")
    lines.append("")
    lines.append(f"SHIP: {report.ship}")
    return "\n".join(lines)


def main() -> int:
    """CLI entry: ``python -m tests.benchmarks._framework.overfit
    --baseline-dir <path> --variant-dir <path> [--a-a-variant-dir <path>]
    [--adapter <name>] [--mode opensre+llm] [--json]``.

    Without ``--a-a-variant-dir`` the report's A/A guard is "not evaluated"
    and ``ship`` is False — provide the second variant run (different
    seed, same config) to satisfy the pre-registered A/A consistency gate.

    Without ``--adapter`` the guards use the default ``OverfitDimensions``
    (CloudOpsBench-shape metadata keys). Pass ``--adapter <name>`` to
    look up the registered adapter's declared dimensions via the
    framework registry — required when running the CLI against case
    files emitted by a non-CloudOpsBench adapter.
    """
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline-dir", type=Path, required=True)
    parser.add_argument("--variant-dir", type=Path, required=True)
    parser.add_argument(
        "--a-a-variant-dir",
        type=Path,
        default=None,
        help=(
            "Optional second variant run (different seed, same config). "
            "Required to satisfy the A/A consistency guard before the "
            "report can ship. Without it, the A/A guard is recorded as "
            "'not evaluated' and the report ship verdict is False."
        ),
    )
    parser.add_argument(
        "--adapter",
        default=None,
        help=(
            "Optional adapter name. When provided, the framework registry "
            "resolves the adapter's declared OverfitDimensions and the "
            "guards consult those metadata keys instead of the default "
            "CloudOpsBench-shape keys. Required when running against case "
            "files emitted by a non-CloudOpsBench adapter."
        ),
    )
    parser.add_argument("--mode", default="opensre+llm")
    parser.add_argument(
        "--json", action="store_true", help="Emit a JSON report instead of human-readable text."
    )
    args = parser.parse_args()

    baseline = load_cells(args.baseline_dir)
    variant = load_cells(args.variant_dir)
    a_a_variant = load_cells(args.a_a_variant_dir) if args.a_a_variant_dir is not None else None
    dimensions: OverfitDimensions | None = None
    if args.adapter is not None:
        # Late import — keeps the overfit module's import surface small
        # for callers that only use the guard functions directly.
        from tests.benchmarks._framework.registry import build_adapter

        dimensions = build_adapter(args.adapter).overfit_dimensions()
    report = analyze(
        baseline,
        variant,
        mode=args.mode,
        a_a_variant=a_a_variant,
        dimensions=dimensions,
    )

    if args.json:
        print(
            json.dumps(
                {
                    "mode": report.mode,
                    "full_corpus_lift": report.full_corpus_lift,
                    "full_corpus_n": report.full_corpus_n,
                    "ship": report.ship,
                    "guards": [
                        {
                            "name": g.name,
                            "passed": g.passed,
                            "measurement": g.measurement,
                            "threshold": g.threshold,
                            "detail": g.detail,
                        }
                        for g in report.guards
                    ],
                },
                indent=2,
                default=str,
            )
        )
    else:
        print(_format_report(report))

    return 0 if report.ship else 1


if __name__ == "__main__":
    raise SystemExit(main())
