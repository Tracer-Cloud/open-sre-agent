"""Unit tests for the IntegrityGuard pre-flight + report validation gates."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from tests.benchmarks._framework.adapters import (
    AlertPayload,
    BenchmarkAdapter,
    BenchmarkCase,
    CaseFilters,
    CaseScore,
    MetricSchema,
    RunContext,
    RunResult,
)
from tests.benchmarks._framework.config import BenchmarkConfig
from tests.benchmarks._framework.integrity import (
    STANDARD_COI_DISCLOSURE,
    BenchmarkReport,
    IntegrityGuard,
    IntegrityViolation,
    make_baseline_report,
)

# --------------------------------------------------------------------------- #
# Minimal honest adapter — passes M3 + M7 by default                          #
# --------------------------------------------------------------------------- #


class _HonestAdapter(BenchmarkAdapter):
    name = "honest"
    version = "0.0.1"
    data_contamination_checked = True

    def load_cases(self, _filters: CaseFilters) -> Iterator[BenchmarkCase]:
        yield BenchmarkCase(case_id="c1", benchmark_name=self.name)

    def build_alert(self, _case: BenchmarkCase) -> AlertPayload:
        return AlertPayload(raw={}, normalized={})

    def build_opensre_integrations(self, _case: BenchmarkCase) -> dict[str, Any]:
        return {}

    def build_baseline_tools(self, _case: BenchmarkCase) -> dict[str, Any]:
        return {}

    def score_case(self, case: BenchmarkCase, _run: RunResult, _context: RunContext) -> CaseScore:
        return CaseScore(case_id=case.case_id, metrics={"a1": 1.0, "grounding": 1.0})

    def metric_schema(self) -> MetricSchema:
        return MetricSchema(
            outcome_metrics=["a1"],
            validity_metrics=["grounding"],
            higher_is_better={"a1": True, "grounding": True},
        )


class _AdapterMissingValidity(_HonestAdapter):
    """Triggers M3 — no validity_metrics declared."""

    def metric_schema(self) -> MetricSchema:
        return MetricSchema(
            outcome_metrics=["a1"],
            validity_metrics=[],
            higher_is_better={"a1": True},
        )


class _AdapterNoContaminationCheck(_HonestAdapter):
    """Triggers M7 — adapter has not declared a contamination check."""

    data_contamination_checked = False


# --------------------------------------------------------------------------- #
# Helpers — config + report factories                                         #
# --------------------------------------------------------------------------- #


def _honest_config(tmp_path: Path, *, with_prereg: bool = True) -> BenchmarkConfig:
    prereg = tmp_path / "prereg.md"
    if with_prereg:
        prereg.write_text("# Expected deltas\n- placeholder\n")
    return BenchmarkConfig.model_validate(
        {
            "benchmark": "honest",
            "modes": ["opensre+llm"],
            "llms": ["claude_sonnet"],
            "model_versions": {"claude_sonnet": "claude-sonnet-4-5-20250929"},
            "seed": 42,
            "cost_budget_usd": 10.0,
            "output_dir": str(tmp_path / "out"),
            "pre_registration_path": str(prereg) if with_prereg else None,
        }
    )


def _honest_report(tmp_path: Path) -> BenchmarkReport:
    cases_dir = tmp_path / "cases"
    cases_dir.mkdir(parents=True, exist_ok=True)
    prereg = tmp_path / "prereg.md"
    prereg.write_text("# Expected deltas\n")
    return make_baseline_report(
        run_id="run-id",
        config_hash="abc",
        started_at="2026-01-01T00:00:00Z",
        ended_at="2026-01-01T00:01:00Z",
        per_stratum={
            "all": {"opensre+llm/claude_sonnet": {"a1": 0.5, "grounding": 0.5}},
            "seen-shape": {"opensre+llm/claude_sonnet": {"a1": 0.6, "grounding": 0.6}},
        },
        reported_metrics=["a1", "grounding"],
        raw_artifacts_dir=cases_dir,
        pre_registration_path=prereg,
        negative_results="No losses recorded in this synthetic test run.",
    )


# --------------------------------------------------------------------------- #
# Pre-flight — happy path                                                      #
# --------------------------------------------------------------------------- #


def test_pre_flight_passes_with_honest_config_and_adapter(tmp_path: Path) -> None:
    guard = IntegrityGuard()
    config = _honest_config(tmp_path)
    adapter = _HonestAdapter()
    guard.pre_flight(config, adapter)


# --------------------------------------------------------------------------- #
# Pre-flight — Mechanism 1 (pre-registration)                                  #
# --------------------------------------------------------------------------- #


def test_pre_flight_rejects_missing_pre_registration_path(tmp_path: Path) -> None:
    config = _honest_config(tmp_path, with_prereg=False)
    with pytest.raises(IntegrityViolation) as exc_info:
        IntegrityGuard().pre_flight(config, _HonestAdapter())
    assert any("M1" in v and "unset" in v for v in exc_info.value.violations)


def test_pre_flight_rejects_nonexistent_pre_registration_file(tmp_path: Path) -> None:
    config = _honest_config(tmp_path)
    # Replace with a path that doesn't exist
    config = config.model_copy(update={"pre_registration_path": tmp_path / "missing.md"})
    with pytest.raises(IntegrityViolation) as exc_info:
        IntegrityGuard().pre_flight(config, _HonestAdapter())
    assert any("does not exist" in v for v in exc_info.value.violations)


def test_pre_flight_rejects_empty_pre_registration_file(tmp_path: Path) -> None:
    empty = tmp_path / "empty.md"
    empty.write_text("")
    config = _honest_config(tmp_path).model_copy(update={"pre_registration_path": empty})
    with pytest.raises(IntegrityViolation) as exc_info:
        IntegrityGuard().pre_flight(config, _HonestAdapter())
    assert any("empty" in v.lower() for v in exc_info.value.violations)


# --------------------------------------------------------------------------- #
# Pre-flight — Mechanism 3 (validity metrics)                                  #
# --------------------------------------------------------------------------- #


def test_pre_flight_rejects_adapter_with_no_validity_metrics(tmp_path: Path) -> None:
    with pytest.raises(IntegrityViolation) as exc_info:
        IntegrityGuard().pre_flight(_honest_config(tmp_path), _AdapterMissingValidity())
    assert any("M3" in v and "validity_metrics" in v for v in exc_info.value.violations)


# --------------------------------------------------------------------------- #
# Pre-flight — Mechanism 6 (seeded selection)                                  #
# --------------------------------------------------------------------------- #


def test_pre_flight_rejects_config_without_seed(tmp_path: Path) -> None:
    config = _honest_config(tmp_path).model_copy(update={"seed": None})
    with pytest.raises(IntegrityViolation) as exc_info:
        IntegrityGuard().pre_flight(config, _HonestAdapter())
    assert any("M6" in v and "seed" in v for v in exc_info.value.violations)


# --------------------------------------------------------------------------- #
# Pre-flight — Mechanism 7 (contamination check declared)                      #
# --------------------------------------------------------------------------- #


def test_pre_flight_rejects_adapter_with_no_contamination_check(tmp_path: Path) -> None:
    with pytest.raises(IntegrityViolation) as exc_info:
        IntegrityGuard().pre_flight(_honest_config(tmp_path), _AdapterNoContaminationCheck())
    assert any("M7" in v and "contamination" in v for v in exc_info.value.violations)


# --------------------------------------------------------------------------- #
# Pre-flight — IntegrityViolation aggregates all failures                      #
# --------------------------------------------------------------------------- #


def test_pre_flight_aggregates_multiple_violations(tmp_path: Path) -> None:
    """Engineer sees ALL violations at once, not one-fix-rerun-discover-next."""
    config = _honest_config(tmp_path, with_prereg=False).model_copy(update={"seed": None})
    with pytest.raises(IntegrityViolation) as exc_info:
        IntegrityGuard().pre_flight(config, _AdapterMissingValidity())
    msg = str(exc_info.value)
    assert "M1" in msg
    assert "M3" in msg
    assert "M6" in msg


# --------------------------------------------------------------------------- #
# Report validation — happy path                                               #
# --------------------------------------------------------------------------- #


def test_report_validation_passes_with_honest_report(tmp_path: Path) -> None:
    IntegrityGuard().report_validation(_honest_report(tmp_path), _HonestAdapter())


# --------------------------------------------------------------------------- #
# Report validation — Mechanism 3 (all declared metrics reported)              #
# --------------------------------------------------------------------------- #


def test_report_validation_rejects_missing_metrics(tmp_path: Path) -> None:
    report = _honest_report(tmp_path)
    # Drop the validity metric from the report — adapter still declares it
    bad_report = BenchmarkReport(**{**report.__dict__, "reported_metrics": ["a1"]})
    with pytest.raises(IntegrityViolation) as exc_info:
        IntegrityGuard().report_validation(bad_report, _HonestAdapter())
    assert any("M3" in v and "grounding" in v for v in exc_info.value.violations)


# --------------------------------------------------------------------------- #
# Report validation — Mechanism 4 (per-stratum)                                #
# --------------------------------------------------------------------------- #


def test_report_validation_rejects_empty_per_stratum(tmp_path: Path) -> None:
    report = _honest_report(tmp_path)
    bad = BenchmarkReport(**{**report.__dict__, "per_stratum": {}})
    with pytest.raises(IntegrityViolation) as exc_info:
        IntegrityGuard().report_validation(bad, _HonestAdapter())
    assert any("M4" in v for v in exc_info.value.violations)


def test_report_validation_rejects_per_stratum_only_all(tmp_path: Path) -> None:
    """A report with only the 'all' stratum is aggregate-only — refused."""
    report = _honest_report(tmp_path)
    bad = BenchmarkReport(
        **{
            **report.__dict__,
            "per_stratum": {"all": report.per_stratum["all"]},
        }
    )
    with pytest.raises(IntegrityViolation) as exc_info:
        IntegrityGuard().report_validation(bad, _HonestAdapter())
    assert any("M4" in v and "seen-shape" in v for v in exc_info.value.violations)


# --------------------------------------------------------------------------- #
# Report validation — Mechanism 5 (raw artifacts published)                    #
# --------------------------------------------------------------------------- #


def test_report_validation_rejects_missing_raw_artifacts_dir(tmp_path: Path) -> None:
    report = _honest_report(tmp_path)
    bad = BenchmarkReport(**{**report.__dict__, "raw_artifacts_dir": None})
    with pytest.raises(IntegrityViolation) as exc_info:
        IntegrityGuard().report_validation(bad, _HonestAdapter())
    assert any("M5" in v for v in exc_info.value.violations)


def test_report_validation_rejects_nonexistent_raw_artifacts_dir(tmp_path: Path) -> None:
    report = _honest_report(tmp_path)
    bad = BenchmarkReport(**{**report.__dict__, "raw_artifacts_dir": tmp_path / "no-such-dir"})
    with pytest.raises(IntegrityViolation) as exc_info:
        IntegrityGuard().report_validation(bad, _HonestAdapter())
    assert any("M5" in v and "does not" in v for v in exc_info.value.violations)


# --------------------------------------------------------------------------- #
# Report validation — Mechanism 9 (negative results)                           #
# --------------------------------------------------------------------------- #


def test_report_validation_rejects_empty_negative_results(tmp_path: Path) -> None:
    report = _honest_report(tmp_path)
    for empty_value in ["", "   ", "\n\n"]:
        bad = BenchmarkReport(**{**report.__dict__, "negative_results": empty_value})
        with pytest.raises(IntegrityViolation) as exc_info:
            IntegrityGuard().report_validation(bad, _HonestAdapter())
        assert any("M9" in v for v in exc_info.value.violations)


# --------------------------------------------------------------------------- #
# Report validation — Mechanism 10 (COI disclosure)                            #
# --------------------------------------------------------------------------- #


def test_report_validation_rejects_empty_coi_disclosure(tmp_path: Path) -> None:
    report = _honest_report(tmp_path)
    bad = BenchmarkReport(**{**report.__dict__, "coi_disclosure": ""})
    with pytest.raises(IntegrityViolation) as exc_info:
        IntegrityGuard().report_validation(bad, _HonestAdapter())
    assert any("M10" in v for v in exc_info.value.violations)


# --------------------------------------------------------------------------- #
# Report validation — Mechanism 1 (pre-registration carried forward)           #
# --------------------------------------------------------------------------- #


def test_report_validation_rejects_missing_pre_registration_in_report(tmp_path: Path) -> None:
    report = _honest_report(tmp_path)
    bad = BenchmarkReport(**{**report.__dict__, "pre_registration_path": None})
    with pytest.raises(IntegrityViolation) as exc_info:
        IntegrityGuard().report_validation(bad, _HonestAdapter())
    assert any("M1" in v for v in exc_info.value.violations)


# --------------------------------------------------------------------------- #
# Report validation — aggregates failures                                      #
# --------------------------------------------------------------------------- #


def test_report_validation_aggregates_multiple_violations(tmp_path: Path) -> None:
    """All violations surfaced at once — engineer fixes everything in one pass."""
    report = _honest_report(tmp_path)
    bad = BenchmarkReport(
        **{
            **report.__dict__,
            "per_stratum": {},
            "negative_results": "",
            "coi_disclosure": "",
        }
    )
    with pytest.raises(IntegrityViolation) as exc_info:
        IntegrityGuard().report_validation(bad, _HonestAdapter())
    msg = str(exc_info.value)
    assert "M4" in msg
    assert "M9" in msg
    assert "M10" in msg


# --------------------------------------------------------------------------- #
# make_baseline_report — default COI disclosure                                #
# --------------------------------------------------------------------------- #


def test_make_baseline_report_supplies_standard_coi_disclosure(tmp_path: Path) -> None:
    cases_dir = tmp_path / "cases"
    cases_dir.mkdir()
    prereg = tmp_path / "prereg.md"
    prereg.write_text("x")
    report = make_baseline_report(
        run_id="r",
        config_hash="h",
        started_at="s",
        ended_at="e",
        per_stratum={"all": {}},
        reported_metrics=[],
        raw_artifacts_dir=cases_dir,
        pre_registration_path=prereg,
        negative_results="ok",
    )
    assert report.coi_disclosure == STANDARD_COI_DISCLOSURE


def test_make_baseline_report_honors_explicit_coi_override(tmp_path: Path) -> None:
    cases_dir = tmp_path / "cases"
    cases_dir.mkdir()
    prereg = tmp_path / "prereg.md"
    prereg.write_text("x")
    custom = "Custom COI: I built this on a Tuesday."
    report = make_baseline_report(
        run_id="r",
        config_hash="h",
        started_at="s",
        ended_at="e",
        per_stratum={"all": {}},
        reported_metrics=[],
        raw_artifacts_dir=cases_dir,
        pre_registration_path=prereg,
        negative_results="ok",
        coi_disclosure=custom,
    )
    assert report.coi_disclosure == custom
