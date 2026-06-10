"""Overfit-attribution analysis for any bench run pair (baseline vs variant).

Implements the four runtime overfit guards from
``exp_structured_outputs_v1.yml``:

  1. **Per-system uniformity** — lift on boutique vs trainticket within 0.05.
     Lift concentrated in one system = the variant learned that system's
     structure.

  2. **Per-stratum uniformity** — no fault category has lift > 2× the median
     category's lift. Concentration = category-specific overfit.

  3. **Per-case attribution clustering** — cells that flipped loss → win on
     the variant are clustered by (system, fault_category, GT-service-prefix).
     If one cluster owns > 60% of flips, that cluster is the overfit
     fingerprint.

  4. **Held-out generalization gate** — held_out_lift / optimize_lift ≥ 0.70
     to ship. < 0.30 → reject as overfit.

Usage:
    uv run python -m tests.benchmarks.cloudopsbench.scripts.overfit_attribution \\
        --baseline-dir /tmp/baseline_cases/ \\
        --variant-dir /tmp/variant_cases/

Each directory should contain the per-case JSON files emitted by the bench
runner (one per cell). The script reads scores + GT + metadata directly;
no LLM calls.
"""

from __future__ import annotations

import argparse
import json
from collections import Counter, defaultdict
from pathlib import Path
from statistics import median
from typing import Any

# Held-out split parameters — MUST match exp_structured_outputs_v1.yml
HELD_OUT_SEED = 42
HELD_OUT_FRAC = 0.20

# Per the pre-reg
SHIP_RATIO_THRESHOLD = 0.70
REJECT_RATIO_THRESHOLD = 0.30
PER_SYSTEM_UNIFORMITY_MAX = 0.05
PER_STRATUM_CONCENTRATION_MAX = 2.0
CLUSTER_CONCENTRATION_MAX = 0.60


def _load_cells(case_dir: Path) -> list[dict[str, Any]]:
    """Read every per-case JSON in ``case_dir``."""
    cells = []
    for fname in sorted(case_dir.glob("*.json")):
        with open(fname) as f:
            d = json.load(f)
        cells.append(d)
    return cells


def _scenario_key(cell: dict[str, Any]) -> tuple[str, str]:
    """(case_id, mode) — the bench's independent unit for paired contrasts."""
    return (cell["case"]["case_id"], cell["run"]["mode"])


def _held_out_case_ids(all_case_ids: list[str]) -> set[str]:
    """Reproducible held-out split — same seed as pre-reg."""
    import random

    rng = random.Random(HELD_OUT_SEED)
    shuffled = sorted(set(all_case_ids))  # stable order before shuffle
    rng.shuffle(shuffled)
    n_held_out = int(len(shuffled) * HELD_OUT_FRAC)
    return set(shuffled[:n_held_out])


def _mean_a1_by_case(cells: list[dict[str, Any]], mode: str) -> dict[str, float]:
    """Average A@1 per case_id for the given mode, averaging across runs."""
    by_case: dict[str, list[float]] = defaultdict(list)
    for cell in cells:
        if cell["run"]["mode"] != mode:
            continue
        by_case[cell["case"]["case_id"]].append(cell["score"]["metrics"]["a1"])
    return {cid: sum(scores) / len(scores) for cid, scores in by_case.items()}


def _aggregate_lift(
    baseline: list[dict[str, Any]],
    variant: list[dict[str, Any]],
    mode: str,
    filter_case_ids: set[str] | None = None,
) -> tuple[float, int]:
    """Mean A@1 lift (variant − baseline) for the mode, with case count.

    If ``filter_case_ids`` is given, restrict to that case-id subset.
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
    attribute_fn,
) -> dict[str, tuple[float, int]]:
    """Lift split by a categorical attribute of the case."""
    base_by_case = _mean_a1_by_case(baseline, mode)
    var_by_case = _mean_a1_by_case(variant, mode)
    attr_of_case: dict[str, str] = {}
    for cell in baseline + variant:
        attr_of_case[cell["case"]["case_id"]] = attribute_fn(cell)
    by_attr: dict[str, list[tuple[float, float]]] = defaultdict(list)
    for cid in set(base_by_case) & set(var_by_case):
        by_attr[attr_of_case[cid]].append((base_by_case[cid], var_by_case[cid]))
    result: dict[str, tuple[float, int]] = {}
    for attr, pairs in by_attr.items():
        base_m = sum(p[0] for p in pairs) / len(pairs)
        var_m = sum(p[1] for p in pairs) / len(pairs)
        result[attr] = (var_m - base_m, len(pairs))
    return result


def _flipped_loss_to_win_clusters(
    baseline: list[dict[str, Any]],
    variant: list[dict[str, Any]],
    mode: str,
) -> dict[tuple[str, str, str], int]:
    """Cluster cells that flip loss (a1=0) → win (a1≥1) by
    (system, fault_category, gt-service-prefix)."""
    base_a1 = {(c["case"]["case_id"], c["run"].get("run_index", 0)): c["score"]["metrics"]["a1"]
               for c in baseline if c["run"]["mode"] == mode}
    var_a1 = {(c["case"]["case_id"], c["run"].get("run_index", 0)): c["score"]["metrics"]["a1"]
              for c in variant if c["run"]["mode"] == mode}
    case_meta = {c["case"]["case_id"]: c["case"]["metadata"] for c in baseline + variant}
    clusters: Counter[tuple[str, str, str]] = Counter()
    for key in base_a1.keys() & var_a1.keys():
        if base_a1[key] == 0 and var_a1[key] >= 1.0:
            case_id = key[0]
            meta = case_meta[case_id]
            gt_fo = meta["ground_truth"].get("fault_object", "")
            # Prefix is everything before the last "-" — keeps service families together
            gt_prefix = gt_fo.rsplit("-", 1)[0] if "-" in gt_fo else gt_fo
            clusters[(meta["system"], meta["fault_category"], gt_prefix)] += 1
    return dict(clusters)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline-dir", type=Path, required=True)
    parser.add_argument("--variant-dir", type=Path, required=True)
    parser.add_argument("--mode", default="opensre+llm",
                        help="Which mode to analyze (default: opensre+llm)")
    args = parser.parse_args()

    baseline = _load_cells(args.baseline_dir)
    variant = _load_cells(args.variant_dir)
    print(f"Loaded {len(baseline)} baseline cells, {len(variant)} variant cells")
    print(f"Analyzing mode: {args.mode}")
    print()

    # 1. Per-system uniformity
    print("=" * 78)
    print("Guard 1: Per-system uniformity (target: lifts within 0.05)")
    print("=" * 78)
    per_system = _per_attribute_lift(
        baseline, variant, args.mode, lambda c: c["case"]["metadata"]["system"]
    )
    lifts = []
    for system, (lift, n) in sorted(per_system.items()):
        print(f"  {system:<20} lift={lift:+.3f} (n={n})")
        lifts.append(lift)
    spread = max(lifts) - min(lifts) if lifts else 0
    verdict = "PASS" if spread <= PER_SYSTEM_UNIFORMITY_MAX else "FAIL"
    print(f"  spread={spread:.3f}  threshold={PER_SYSTEM_UNIFORMITY_MAX}  {verdict}")
    print()

    # 2. Per-stratum uniformity
    print("=" * 78)
    print("Guard 2: Per-stratum uniformity (target: no category > 2× median lift)")
    print("=" * 78)
    per_stratum = _per_attribute_lift(
        baseline, variant, args.mode, lambda c: c["case"]["metadata"]["fault_category"]
    )
    stratum_lifts = []
    for stratum, (lift, n) in sorted(per_stratum.items()):
        print(f"  {stratum:<15} lift={lift:+.3f} (n={n})")
        stratum_lifts.append(lift)
    pos_lifts = [l for l in stratum_lifts if l > 0]
    if pos_lifts:
        med = median(pos_lifts)
        max_lift = max(pos_lifts)
        ratio = max_lift / med if med > 0 else float("inf")
        verdict = "PASS" if ratio <= PER_STRATUM_CONCENTRATION_MAX else "FAIL"
        print(f"  max/median ratio={ratio:.2f}  threshold={PER_STRATUM_CONCENTRATION_MAX}x  {verdict}")
    else:
        print("  No positive stratum lifts to check ratio — skipping uniformity check.")
    print()

    # 3. Per-case attribution clustering
    print("=" * 78)
    print(f"Guard 3: Flipped-to-win cluster concentration (target: ≤ {CLUSTER_CONCENTRATION_MAX:.0%})")
    print("=" * 78)
    clusters = _flipped_loss_to_win_clusters(baseline, variant, args.mode)
    total_flips = sum(clusters.values())
    if total_flips == 0:
        print("  No loss→win flips — nothing to cluster.")
    else:
        print(f"  Total flips: {total_flips}")
        top_clusters = sorted(clusters.items(), key=lambda kv: -kv[1])[:5]
        for cluster_key, count in top_clusters:
            pct = count / total_flips
            print(f"    {cluster_key} → {count} flips ({pct:.1%})")
        max_concentration = max(c / total_flips for c in clusters.values())
        verdict = "PASS" if max_concentration <= CLUSTER_CONCENTRATION_MAX else "FAIL"
        print(f"  max concentration={max_concentration:.1%}  "
              f"threshold={CLUSTER_CONCENTRATION_MAX:.0%}  {verdict}")
    print()

    # 4. Held-out generalization gate
    print("=" * 78)
    print(f"Guard 4: Held-out generalization (target: held_out_lift / optimize_lift ≥ {SHIP_RATIO_THRESHOLD})")
    print("=" * 78)
    all_case_ids = list({c["case"]["case_id"] for c in baseline + variant})
    held_out = _held_out_case_ids(all_case_ids)
    optimize = set(all_case_ids) - held_out
    opt_lift, opt_n = _aggregate_lift(baseline, variant, args.mode, optimize)
    held_lift, held_n = _aggregate_lift(baseline, variant, args.mode, held_out)
    print(f"  optimize stratum  lift={opt_lift:+.3f} (n={opt_n})")
    print(f"  held-out stratum  lift={held_lift:+.3f} (n={held_n})")
    if opt_lift > 0:
        ratio = held_lift / opt_lift
        if ratio >= SHIP_RATIO_THRESHOLD:
            verdict = "PASS (ship)"
        elif ratio < REJECT_RATIO_THRESHOLD:
            verdict = "FAIL (reject as overfit)"
        else:
            verdict = "WARN (between ship and reject; inspect)"
        print(f"  held/optimize ratio={ratio:.2f}  ship≥{SHIP_RATIO_THRESHOLD}  "
              f"reject<{REJECT_RATIO_THRESHOLD}  {verdict}")
    else:
        print("  No positive optimize lift — gate doesn't apply (no lift to validate).")
    print()

    # Aggregate summary
    print("=" * 78)
    print("AGGREGATE")
    print("=" * 78)
    full_lift, full_n = _aggregate_lift(baseline, variant, args.mode)
    print(f"  Full corpus  lift={full_lift:+.3f} (n={full_n})")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
