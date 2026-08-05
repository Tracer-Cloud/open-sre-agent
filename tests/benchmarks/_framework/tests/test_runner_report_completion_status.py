"""report.json must record whether the run it describes completed.

Before this fix, ``_report_to_dict`` never saw ``RunOutcome.aborted`` /
``abort_reason`` — a halted run's report.json was key-identical to a
complete run's. The exit code was the only signal, and the exit code
never reaches the artifact: ``entrypoint.sh`` deliberately disables
``set -e`` so the S3 sync still runs after a non-zero bench exit. See
issue #4567.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import Any
from unittest.mock import patch

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
from tests.benchmarks._framework.cost import CostTracker
from tests.benchmarks._framework.integrity import BenchmarkReport
from tests.benchmarks._framework.runner import BenchmarkRunner, _report_to_dict


class _TinyAdapter(BenchmarkAdapter):
    """Minimal adapter, just valid enough to drive a full ``_run_inner``."""

    name = "tiny"
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
        return CaseScore(case_id=case.case_id, metrics={"a1": 1.0})

    def metric_schema(self) -> MetricSchema:
        return MetricSchema(outcome_metrics=["a1"], higher_is_better={"a1": True})


def _config(tmp_path: Path, *, llm: str, model_version: str) -> BenchmarkConfig:
    return BenchmarkConfig.model_validate(
        {
            "benchmark": "tiny",
            "modes": ["opensre+llm"],
            "llms": [llm],
            "model_versions": {llm: model_version},
            "seed": 42,
            "cost_budget_usd": 10.0,
            "output_dir": str(tmp_path / "out"),
        }
    )


def _read_report_json(outcome: Any) -> dict[str, Any]:
    import json

    report_path = outcome.report.raw_artifacts_dir.parent / "report.json"
    return json.loads(report_path.read_text(encoding="utf-8"))


# --------------------------------------------------------------------------- #
# _report_to_dict — unit level                                                #
# --------------------------------------------------------------------------- #


def test_report_to_dict_defaults_to_not_aborted() -> None:
    """Existing callers that don't pass the new kwargs must see the old
    always-complete-looking shape, just with the two new keys appended."""
    report = BenchmarkReport(
        run_id="r1",
        config_hash="hash",
        started_at="t0",
        ended_at="t1",
        per_stratum={},
    )
    data = _report_to_dict(report, CostTracker(budget_usd=10.0))
    assert data["aborted"] is False
    assert data["abort_reason"] is None


def test_report_to_dict_records_abort_state() -> None:
    report = BenchmarkReport(
        run_id="r1",
        config_hash="hash",
        started_at="t0",
        ended_at="t1",
        per_stratum={},
    )
    data = _report_to_dict(
        report,
        CostTracker(budget_usd=10.0),
        aborted=True,
        abort_reason="LLM dispatch failed: 'gpt-5' requires env var OPENAI_API_KEY",
    )
    assert data["aborted"] is True
    assert data["abort_reason"] == "LLM dispatch failed: 'gpt-5' requires env var OPENAI_API_KEY"


# --------------------------------------------------------------------------- #
# End-to-end — report.json on disk                                            #
# --------------------------------------------------------------------------- #


def test_aborted_run_writes_aborted_true_to_report_json(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A missing API key halts the run; report.json must say so.

    Mirrors the issue's own repro: no key is needed for the halted arm,
    an absent key is what triggers the halt.
    """
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    runner = BenchmarkRunner(
        config=_config(tmp_path, llm="gpt-5", model_version="gpt-5-2025-08-07"),
        adapter=_TinyAdapter(),
    )

    outcome = runner.run_without_integrity()

    assert outcome.aborted is True
    data = _read_report_json(outcome)
    assert data["aborted"] is True
    assert data["abort_reason"] is not None
    assert "OPENAI_API_KEY" in data["abort_reason"]


def test_complete_run_writes_aborted_false_to_report_json(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    runner = BenchmarkRunner(
        config=_config(tmp_path, llm="claude-4-sonnet", model_version="claude-sonnet-4-5-20250929"),
        adapter=_TinyAdapter(),
    )
    with patch(
        "tools.investigation.capability.run_investigation",
        return_value={"root_cause": "ok", "report": "ok", "evidence_entries": []},
    ):
        outcome = runner.run_without_integrity()

    assert outcome.aborted is False
    data = _read_report_json(outcome)
    assert data["aborted"] is False
    assert data["abort_reason"] is None
