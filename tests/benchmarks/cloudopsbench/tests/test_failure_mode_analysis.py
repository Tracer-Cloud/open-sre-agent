"""Tests for the Track-2 failure-mode analysis.

Pin the breakdowns we'll use to pick the next lever from the powered run:
  - only the opensre+llm arm feeds the per-category / per-system tables
  - localization-vs-labeling separates "wrong label" from "wrong place"
  - the control contrast is a PAIRED scenario delta and degrades gracefully
    when an arm wasn't run (the single-arm pilot)
"""

from __future__ import annotations

import json
from pathlib import Path

from tests.benchmarks.cloudopsbench.failure_mode_analysis import (
    _cell_system,
    _metric_by_case,
    _primary_cells,
    main,
)


def _cell(
    case_id: str,
    *,
    mode: str = "opensre+llm",
    llm: str = "gpt-4o",
    system: str = "boutique",
    category: str = "runtime",
    **metrics: float,
) -> dict[str, object]:
    return {
        "case": {
            "case_id": case_id,
            "metadata": {"system": system, "fault_category": category},
        },
        "run": {"mode": mode, "llm": llm},
        "score": {"metrics": metrics},
    }


def _write_cases(tmp_path: Path, cells: list[dict[str, object]]) -> Path:
    cases_dir = tmp_path / "cases"
    cases_dir.mkdir(parents=True)
    for i, cell in enumerate(cells):
        (cases_dir / f"cell_{i}.json").write_text(json.dumps(cell))
    return tmp_path


def test_cell_system_reads_metadata() -> None:
    assert _cell_system(_cell("c1", system="trainticket")) == "trainticket"


def test_metric_by_case_groups_seeds() -> None:
    cells = [_cell("c1", a1=1.0), _cell("c1", a1=0.0), _cell("c2", a1=1.0)]
    grouped = _metric_by_case(cells, "a1")
    assert sorted(grouped["c1"]) == [0.0, 1.0]
    assert grouped["c2"] == [1.0]


def test_primary_cells_filters_to_opensre_arm() -> None:
    cells = [
        _cell("c1", mode="opensre+llm", a1=1.0),
        _cell("c1", mode="llm_alone", a1=0.0),
        _cell("c2", mode="llm_alone_pure", a1=0.0),
    ]
    primary = _primary_cells(cells)
    assert len(primary) == 1
    assert primary[0]["run"]["mode"] == "opensre+llm"


def test_main_renders_breakdowns_and_right_place_wrong_label(tmp_path: Path, capsys) -> None:
    # c1: right place (object_a1=1) but wrong label (a1=0) → labeling failure
    # c2: wrong place (object_a1=0, a1=0) → mislocalization
    run_dir = _write_cases(
        tmp_path,
        [
            _cell(
                "c1",
                system="trainticket",
                category="runtime",
                a1=0.0,
                object_a1=1.0,
                cov=0.5,
                steps=9,
            ),
            _cell(
                "c2", system="boutique", category="startup", a1=0.0, object_a1=0.0, cov=0.9, steps=8
            ),
        ],
    )
    assert main([str(run_dir)]) == 0
    out = capsys.readouterr().out
    assert "Localization vs labeling" in out
    assert "Per fault-category" in out
    assert "Per system" in out
    assert "trainticket" in out and "boutique" in out
    # one right-place/wrong-label and one mislocalized of two scenarios
    assert "right place / wrong label: 1/2" in out
    assert "wrong place (mislocalized): 1/2" in out


def test_main_control_contrast_paired_delta(tmp_path: Path, capsys) -> None:
    # Same scenario in two arms: opensre+llm a1=1.0, llm_alone a1=0.0 → +1.0 delta
    run_dir = _write_cases(
        tmp_path,
        [
            _cell("c1", mode="opensre+llm", a1=1.0, object_a1=1.0),
            _cell("c1", mode="llm_alone", a1=0.0, object_a1=0.0),
        ],
    )
    assert main([str(run_dir)]) == 0
    out = capsys.readouterr().out
    assert "Control contrast" in out
    assert "vs llm_alone" in out
    assert "1.000" in out  # paired delta magnitude
    # the unrun pure arm is reported as absent, not crashed
    assert "llm_alone_pure" in out and "arm not run" in out


def test_main_errors_without_cases_dir(tmp_path: Path, capsys) -> None:
    assert main([str(tmp_path)]) == 1
