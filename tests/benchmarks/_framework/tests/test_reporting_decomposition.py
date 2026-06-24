"""Tests for the Track-2 decomposition math in reporting.

The decomposition answers "where does the accuracy go?" — its three
sub-tables (control contrast, localization-vs-labeling, per-category) are
the analytical payload of a powered run. The control delta in particular
is the single number that isolates opensre's contribution from the model's
intrinsic skill, so its pairing + CI logic must be pinned.
"""

from __future__ import annotations

from tests.benchmarks._framework.reporting import (
    _category_a1,
    _cells_by_llm_mode,
    _control_contrast_rows,
    _paired_scenario_deltas,
)


def _cell(
    case_id: str,
    mode: str,
    a1: float,
    *,
    llm: str = "gpt-4o",
    category: str = "runtime",
) -> dict[str, object]:
    return {
        "case": {"case_id": case_id, "metadata": {"fault_category": category}},
        "run": {"llm": llm, "mode": mode},
        "score": {"metrics": {"a1": a1}},
    }


# --------------------------------------------------------------------------- #
# _cells_by_llm_mode — grouping splits on mode                                #
# --------------------------------------------------------------------------- #


def test_cells_by_llm_mode_splits_modes_under_each_llm() -> None:
    cells = [
        _cell("s1", "opensre+llm", 1.0),
        _cell("s1", "llm_alone", 0.0),
        _cell("s2", "opensre+llm", 1.0, llm="gpt-5"),
    ]
    grouped = _cells_by_llm_mode(cells)
    assert set(grouped["gpt-4o"].keys()) == {"opensre+llm", "llm_alone"}
    assert set(grouped["gpt-5"].keys()) == {"opensre+llm"}
    assert len(grouped["gpt-4o"]["opensre+llm"]) == 1


def test_cells_by_llm_mode_skips_load_errors() -> None:
    cells = [_cell("s1", "opensre+llm", 1.0), {"_load_error": "/bad.json"}]
    grouped = _cells_by_llm_mode(cells)
    assert list(grouped.keys()) == ["gpt-4o"]


# --------------------------------------------------------------------------- #
# _paired_scenario_deltas — pairing + seed averaging                          #
# --------------------------------------------------------------------------- #


def test_paired_deltas_only_count_scenarios_in_both_modes() -> None:
    """s2 exists only in opensre+llm — it must be dropped from the paired set."""
    cells = [
        _cell("s1", "opensre+llm", 1.0),
        _cell("s1", "llm_alone", 0.0),
        _cell("s2", "opensre+llm", 1.0),  # unpaired — no llm_alone counterpart
    ]
    deltas = _paired_scenario_deltas(cells, "gpt-4o", "a1", "opensre+llm", "llm_alone")
    assert deltas == [1.0]


def test_paired_deltas_average_seeds_before_differencing() -> None:
    """3 opensre seeds [1,1,0] (mean 2/3) vs 1 baseline seed [0] → +2/3."""
    cells = [
        _cell("s1", "opensre+llm", 1.0),
        _cell("s1", "opensre+llm", 1.0),
        _cell("s1", "opensre+llm", 0.0),
        _cell("s1", "llm_alone", 0.0),
    ]
    deltas = _paired_scenario_deltas(cells, "gpt-4o", "a1", "opensre+llm", "llm_alone")
    assert deltas == [2 / 3]


def test_paired_deltas_filter_by_llm() -> None:
    cells = [
        _cell("s1", "opensre+llm", 1.0, llm="gpt-4o"),
        _cell("s1", "llm_alone", 0.0, llm="gpt-4o"),
        _cell("s1", "opensre+llm", 0.0, llm="gpt-5"),
        _cell("s1", "llm_alone", 0.0, llm="gpt-5"),
    ]
    assert _paired_scenario_deltas(cells, "gpt-5", "a1", "opensre+llm", "llm_alone") == [0.0]


# --------------------------------------------------------------------------- #
# _control_contrast_rows — verdict logic                                      #
# --------------------------------------------------------------------------- #


def test_control_contrast_omits_llm_without_both_arms() -> None:
    cells = [_cell("s1", "opensre+llm", 1.0)]
    assert _control_contrast_rows(cells, _cells_by_llm_mode(cells)) == []


def test_control_contrast_flags_no_effect_when_ci_contains_zero() -> None:
    """All scenarios tie (delta=0) → CI is [0,0], verdict must say no effect."""
    cells = []
    for sid in ["s1", "s2", "s3", "s4"]:
        cells.append(_cell(sid, "opensre+llm", 0.5))
        cells.append(_cell(sid, "llm_alone", 0.5))
    rows = _control_contrast_rows(cells, _cells_by_llm_mode(cells))
    assert len(rows) == 1
    llm, mean, lo, hi, n, verdict = rows[0]
    assert mean == 0.0
    assert lo <= 0.0 <= hi
    assert "no significant effect" in verdict


def test_control_contrast_reports_opensre_helps_when_ci_excludes_zero() -> None:
    cells = []
    for sid, op in [("s1", 1.0), ("s2", 1.0), ("s3", 1.0), ("s4", 1.0)]:
        cells.append(_cell(sid, "opensre+llm", op))
        cells.append(_cell(sid, "llm_alone", 0.0))
    rows = _control_contrast_rows(cells, _cells_by_llm_mode(cells))
    _, mean, lo, hi, n, verdict = rows[0]
    assert mean == 1.0
    assert lo > 0.0
    assert verdict == "opensre helps"
    assert n == 4


# --------------------------------------------------------------------------- #
# _category_a1 — per-category breakdown                                       #
# --------------------------------------------------------------------------- #


def test_category_a1_buckets_by_fault_category() -> None:
    cells = [
        _cell("s1", "opensre+llm", 1.0, category="runtime"),
        _cell("s2", "opensre+llm", 0.0, category="runtime"),
        _cell("s3", "opensre+llm", 1.0, category="startup"),
    ]
    by_lm = _cells_by_llm_mode(cells)
    cat_map = _category_a1(by_lm, "gpt-4o", "opensre+llm")
    assert cat_map["runtime"] == (0.5, 2)
    assert cat_map["startup"] == (1.0, 1)
