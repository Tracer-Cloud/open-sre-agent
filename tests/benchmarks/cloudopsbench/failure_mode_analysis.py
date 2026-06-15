"""Track-2 failure-mode analysis over a finished run's per-case artifacts.

Read-only post-hoc analysis of ``run_dir/cases/*.json``. Answers the "where and
why does opensre lose?" questions the headline table can't, so the powered
full-corpus run turns into actionable next levers rather than a single number.

Four breakdowns, all scenario-clustered (seeds within a scenario averaged
first — the scenario is the independent unit):

  1. Localization vs labeling — a1 (strict triple) vs object_a1 (right service)
     vs partial_a1 (object+root_cause). The a1−object_a1 gap is "right place,
     wrong label"; the object_a1 deficit is "wrong place" (true mislocalization).

  2. Per fault-category a1 — which fault families the agent fails on (the paper
     reports Performance/Admission as universally hard).

  3. Per system a1 — boutique (10 services) vs train-ticket (40 services); the
     pilot showed train-ticket is the hard system (attention degrades at scale).

  4. Control contrast — paired (opensre+llm − llm_alone) and
     (opensre+llm − llm_alone_pure) deltas on a1, so any "opensre helps" claim
     is attributable to the floor lever vs the full stack. Absent arms are
     skipped gracefully (the pilot has only opensre+llm).

Usage:

    python -m tests.benchmarks.cloudopsbench.failure_mode_analysis \
        .bench-results/cloudopsbench_v1_openai/<run_id>/
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any

from tests.benchmarks._framework.reporting import (
    _cell_category,
    _cell_mode,
    _cells_by_llm_mode,
    _load_cells,
    _paired_scenario_deltas,
    _scenario_means,
)

_PRIMARY_MODE = "opensre+llm"
_CONTROL_MODES = ["llm_alone", "llm_alone_pure"]


def _mean(values: list[float]) -> float | None:
    return sum(values) / len(values) if values else None


def _fmt(value: float | None) -> str:
    return f"{value:.3f}" if value is not None else "  -  "


def _metric_by_case(cells: list[dict[str, Any]], metric: str) -> dict[str, list[float]]:
    """Group one metric by case_id (for paired right-place/wrong-label counts)."""
    out: dict[str, list[float]] = {}
    for cell in cells:
        value = cell.get("score", {}).get("metrics", {}).get(metric)
        if isinstance(value, (int, float)):
            case_id = cell.get("case", {}).get("case_id", "(unknown)")
            out.setdefault(case_id, []).append(float(value))
    return out


def _cell_system(cell: dict[str, Any]) -> str:
    return cell.get("case", {}).get("metadata", {}).get("system", "(unknown)")


def _primary_cells(cells: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [c for c in cells if "_load_error" not in c and _cell_mode(c) == _PRIMARY_MODE]


def _localization_vs_labeling(cells: list[dict[str, Any]], llm: str) -> None:
    print(f"\n## Localization vs labeling — {llm} ({_PRIMARY_MODE})")
    print(f"{'metric':<12}{'mean':>8}  interpretation")
    print("-" * 60)
    rows = [
        ("a1", "strict triple match (taxonomy+object+root_cause)"),
        ("object_a1", "right service localized (object only)"),
        ("partial_a1", "object + root_cause (taxonomy ignored)"),
    ]
    for metric, desc in rows:
        m = _mean(_scenario_means(cells, metric))
        print(f"{metric:<12}{_fmt(m):>8}  {desc}")

    # Right-place / wrong-label: object correct but strict a1 wrong, per scenario.
    a1_by_case = _metric_by_case(cells, "a1")
    obj_by_case = _metric_by_case(cells, "object_a1")
    right_place_wrong_label = 0
    wrong_place = 0
    total = 0
    for case_id in a1_by_case.keys() & obj_by_case.keys():
        a1 = sum(a1_by_case[case_id]) / len(a1_by_case[case_id])
        obj = sum(obj_by_case[case_id]) / len(obj_by_case[case_id])
        total += 1
        if obj >= 0.5 and a1 < 0.5:
            right_place_wrong_label += 1
        elif obj < 0.5:
            wrong_place += 1
    if total:
        print(
            f"\n  right place / wrong label: {right_place_wrong_label}/{total} scenarios "
            f"({right_place_wrong_label / total:.0%}) — fix with a labeling lever"
        )
        print(
            f"  wrong place (mislocalized): {wrong_place}/{total} scenarios "
            f"({wrong_place / total:.0%}) — fix with an exploration/coverage lever"
        )


def _breakdown(cells: list[dict[str, Any]], llm: str, key_fn: Any, title: str) -> None:
    print(f"\n## {title} — {llm} ({_PRIMARY_MODE})")
    groups: dict[str, list[dict[str, Any]]] = {}
    for cell in cells:
        groups.setdefault(key_fn(cell), []).append(cell)
    print(f"{'group':<28}{'n':>5}{'a1':>8}{'object_a1':>11}{'cov':>8}{'steps':>8}")
    print("-" * 68)
    for name in sorted(groups):
        grp = groups[name]
        n_scen = len({c.get("case", {}).get("case_id") for c in grp})
        a1 = _mean(_scenario_means(grp, "a1"))
        obj = _mean(_scenario_means(grp, "object_a1"))
        cov = _mean(_scenario_means(grp, "cov"))
        steps = _mean(_scenario_means(grp, "steps"))
        print(f"{name:<28}{n_scen:>5}{_fmt(a1):>8}{_fmt(obj):>11}{_fmt(cov):>8}{_fmt(steps):>8}")


def _control_contrast(cells: list[dict[str, Any]], llm: str) -> None:
    print(f"\n## Control contrast — {llm} (paired scenario deltas on a1)")
    modes_present = {_cell_mode(c) for c in cells if c.get("run", {}).get("llm") == llm}
    if _PRIMARY_MODE not in modes_present:
        print("  (no opensre+llm cells for this LLM — skipping)")
        return
    any_control = False
    for control in _CONTROL_MODES:
        if control not in modes_present:
            print(f"  vs {control:<16}: (arm not run)")
            continue
        any_control = True
        deltas = _paired_scenario_deltas(cells, llm, "a1", _PRIMARY_MODE, control)
        delta = _mean(deltas)
        sign = "+" if (delta or 0) >= 0 else ""
        meaning = {
            "llm_alone": "lift from MIN_TOOL_CALLS floor alone",
            "llm_alone_pure": "lift from the full opensre stack",
        }.get(control, "")
        print(
            f"  vs {control:<16}: {sign}{_fmt(delta)} over {len(deltas)} paired "
            f"scenarios — {meaning}"
        )
    if not any_control:
        print(
            "  (no control arms in this run — single-arm pilot; run the v1 "
            "config's llm_alone / llm_alone_pure to attribute the lift)"
        )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("run_dir", help="A finished run dir containing cases/.")
    args = parser.parse_args(argv)

    run_dir = Path(args.run_dir)
    cases_dir = run_dir / "cases"
    if not cases_dir.is_dir():
        print(f"  x {cases_dir} not found", file=sys.stderr)
        return 1

    all_cells = _load_cells(cases_dir)
    by_llm_mode = _cells_by_llm_mode(all_cells)
    if not by_llm_mode:
        print(f"  x no scorable cells under {cases_dir}", file=sys.stderr)
        return 1

    print(f"# Track-2 failure-mode analysis — {run_dir.name}")
    print(
        f"# {len([c for c in all_cells if '_load_error' not in c])} cells, "
        f"{len(by_llm_mode)} LLM(s): {', '.join(sorted(by_llm_mode))}"
    )

    for llm in sorted(by_llm_mode):
        llm_cells = [c for c in all_cells if c.get("run", {}).get("llm") == llm]
        primary = _primary_cells(llm_cells)
        if primary:
            _localization_vs_labeling(primary, llm)
            _breakdown(primary, llm, _cell_category, "Per fault-category")
            _breakdown(primary, llm, _cell_system, "Per system")
        _control_contrast(llm_cells, llm)

    print(
        "\n# Read: a1−object_a1 gap = labeling problem (lever #4 cite/label); "
        "low object_a1 = localization problem (coverage/exploration lever)."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
