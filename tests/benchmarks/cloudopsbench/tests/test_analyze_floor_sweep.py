"""Tests for the MIN_TOOL_CALLS floor-sweep summarizer.

These pin the parts that decide which floor we lock into the powered run:
  - the floor is read from each run's provenance (so rows are labeled right)
  - per-cell values are scenario-clustered means (seeds averaged first), the
    same statistic the headline uses — not a raw per-seed average
  - only completed runs (a cases/ dir) are picked up
"""

from __future__ import annotations

import json
from pathlib import Path

from tests.benchmarks.cloudopsbench.analyze_floor_sweep import (
    _floor_for_run,
    _mean,
    _run_dirs,
    main,
)


def _write_run(
    sweep_dir: Path,
    name: str,
    *,
    floor: int | None,
    cells: list[dict[str, object]],
) -> Path:
    run_dir = sweep_dir / name
    cases_dir = run_dir / "cases"
    cases_dir.mkdir(parents=True)
    if floor is not None:
        (run_dir / "provenance.json").write_text(
            json.dumps({"run_inputs": {"min_tool_calls": floor}})
        )
    for i, cell in enumerate(cells):
        (cases_dir / f"cell_{i}.json").write_text(json.dumps(cell))
    return run_dir


def _cell(case_id: str, **metrics: float) -> dict[str, object]:
    return {"case": {"case_id": case_id}, "score": {"metrics": metrics}}


def test_floor_for_run_reads_provenance(tmp_path: Path) -> None:
    run = _write_run(tmp_path, "r1", floor=5, cells=[_cell("c1", a1=1.0)])
    assert _floor_for_run(run) == 5


def test_floor_for_run_none_when_missing_or_invalid(tmp_path: Path) -> None:
    run = _write_run(tmp_path, "r1", floor=None, cells=[_cell("c1", a1=1.0)])
    assert _floor_for_run(run) is None
    (run / "provenance.json").write_text("{not json")
    assert _floor_for_run(run) is None


def test_run_dirs_only_returns_dirs_with_cases(tmp_path: Path) -> None:
    _write_run(tmp_path, "good", floor=8, cells=[_cell("c1", a1=1.0)])
    (tmp_path / "not_a_run").mkdir()  # no cases/ → ignored
    (tmp_path / "loose.txt").write_text("x")
    dirs = _run_dirs(tmp_path)
    assert [d.name for d in dirs] == ["good"]


def test_mean_helper() -> None:
    assert _mean([1.0, 0.0]) == 0.5
    assert _mean([]) is None


def test_main_scenario_clusters_then_sorts_by_floor(tmp_path: Path, capsys) -> None:
    # floor 8: two seeds of ONE scenario → scenario mean = 0.5 (not 2 obs of 1/0)
    _write_run(
        tmp_path,
        "run_floor8",
        floor=8,
        cells=[_cell("c1", a1=1.0), _cell("c1", a1=0.0)],
    )
    # floor 3: one scenario a1=1.0
    _write_run(tmp_path, "run_floor3", floor=3, cells=[_cell("c1", a1=1.0)])

    exit_code = main([str(tmp_path), "--paper", "gpt-4o"])
    out = capsys.readouterr().out
    assert exit_code == 0

    lines = [ln for ln in out.splitlines() if ln.strip()]
    floor_rows = [ln for ln in lines if ln.split()[0] in {"3", "8"}]
    # sorted ascending: floor 3 before floor 8
    assert floor_rows[0].startswith("3")
    assert floor_rows[1].startswith("8")
    # floor 8 row: scenario-clustered a1 mean = 0.5
    assert "0.500" in floor_rows[1]
    # paper row present with the known gpt-4o Base a1
    assert any(ln.startswith("paper") and "0.490" in ln for ln in lines)


def test_main_errors_on_empty_dir(tmp_path: Path, capsys) -> None:
    assert main([str(tmp_path)]) == 1
