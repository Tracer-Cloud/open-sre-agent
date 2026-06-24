"""Summarize a MIN_TOOL_CALLS floor sweep into one floor-vs-metric table.

Run after sweeping ``cloudopsbench_floorsweep_openai.yml`` over several
``BENCH_MIN_TOOL_CALLS`` values. Each run wrote a timestamped subdir under the
config's ``output_dir`` with a ``provenance.json`` (the floor lives at
``run_inputs.min_tool_calls``) and a ``cases/`` dir of per-case scores.

The question this answers: does lowering the floor lift the process metrics
opensre is weak on (rel/exact) and cut over-exploration (steps), WITHOUT
dropping the outcome metric (a1)? Each cell is a scenario-clustered mean (seeds
within a scenario averaged first), matching the report's headline statistic.

Usage:

    python -m tests.benchmarks.cloudopsbench.analyze_floor_sweep \
        .bench-results/cloudopsbench_floorsweep_openai/

Add ``--paper gpt-4o`` to print the paper Table-4 Base row alongside as the
reference target.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from tests.benchmarks._framework.reporting import (
    _PAPER_BASELINE,
    _load_cells,
    _scenario_means,
)

# Process + outcome metrics the sweep is meant to move. a1 is the guardrail
# (must not drop); rel/exact/steps/cov are the over-exploration signal.
_SWEEP_METRICS = [
    "a1",
    "a3",
    "exact",
    "in_order",
    "any_order",
    "rel",
    "cov",
    "steps",
    "iac",
    "mtti",
]


def _floor_for_run(run_dir: Path) -> int | None:
    """Read the resolved MIN_TOOL_CALLS floor from a run's provenance.json."""
    prov_path = run_dir / "provenance.json"
    if not prov_path.exists():
        return None
    try:
        prov = json.loads(prov_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None
    value = prov.get("run_inputs", {}).get("min_tool_calls")
    return value if isinstance(value, int) else None


def _run_dirs(sweep_dir: Path) -> list[Path]:
    """Every immediate subdir that looks like a completed run."""
    return sorted(d for d in sweep_dir.iterdir() if d.is_dir() and (d / "cases").is_dir())


def _mean(values: list[float]) -> float | None:
    return sum(values) / len(values) if values else None


def _fmt(value: float | None) -> str:
    return f"{value:.3f}" if value is not None else "  -  "


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("sweep_dir", help="Directory holding the per-floor run subdirs.")
    parser.add_argument(
        "--paper",
        default=None,
        help="LLM key (e.g. gpt-4o) to print the paper Table-4 Base row as a target.",
    )
    args = parser.parse_args(argv)

    sweep_dir = Path(args.sweep_dir)
    if not sweep_dir.is_dir():
        print(f"  x {sweep_dir} is not a directory", file=sys.stderr)
        return 1

    rows: list[tuple[int | None, str, dict[str, float | None], int]] = []
    for run_dir in _run_dirs(sweep_dir):
        cells = _load_cells(run_dir / "cases")
        if not cells:
            continue
        floor = _floor_for_run(run_dir)
        means = {m: _mean(_scenario_means(cells, m)) for m in _SWEEP_METRICS}
        n_scen = len({c.get("case", {}).get("case_id") for c in cells if "_load_error" not in c})
        rows.append((floor, run_dir.name, means, n_scen))

    if not rows:
        print(f"  x no completed runs found under {sweep_dir}", file=sys.stderr)
        return 1

    rows.sort(key=lambda r: (r[0] is None, r[0]))

    header = ["floor", "n", *_SWEEP_METRICS]
    widths = [max(7, len(h)) for h in header]
    print("  ".join(h.ljust(w) for h, w in zip(header, widths, strict=True)))
    print("  ".join("-" * w for w in widths))
    for floor, _name, means, n_scen in rows:
        cells_str = [
            (str(floor) if floor is not None else "?"),
            str(n_scen),
            *[_fmt(means[m]) for m in _SWEEP_METRICS],
        ]
        print("  ".join(c.ljust(w) for c, w in zip(cells_str, widths, strict=True)))

    if args.paper:
        paper = _PAPER_BASELINE.get(args.paper.strip().lower())
        if paper:
            cells_str = [
                "paper",
                "452",
                *[_fmt(paper.get(m)) for m in _SWEEP_METRICS],
            ]
            print("  ".join(c.ljust(w) for c, w in zip(cells_str, widths, strict=True)))
        else:
            print(f"  (no paper Base row for {args.paper!r})", file=sys.stderr)

    print()
    print("Read: a1 is the guardrail (must NOT drop as floor falls); rel/exact up")
    print("and steps down = less over-exploration. Pick the lowest floor that holds a1.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
