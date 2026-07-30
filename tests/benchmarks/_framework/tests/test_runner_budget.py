"""Regression tests for BenchmarkRunner._run_one_cell exception filtering.

The runner's per-cell handler intentionally lets a few exception types
propagate up rather than recording them as cell-level failures:

  - CostBudgetExceeded: the run-fatal budget cap. Swallowing it would let
    the run continue past the configured ceiling.
  - UnknownModel: a pre-flight problem (model missing from pricing table).
    Should halt the run, not mask as a per-case failure.

All other exceptions from run_investigation are caught and recorded so
one bad cell doesn't kill a multi-thousand-cell grid.

See Greptile P1 review on the bench-image PR for the original report.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest

from core.llm.shared.llm_retry import LLMCreditExhaustedError
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
from tests.benchmarks._framework.cost import CostBudgetExceeded, UnknownModel
from tests.benchmarks._framework.llm_dispatch import LLM_SPECS
from tests.benchmarks._framework.runner import BenchmarkRunner


class _TinyAdapter(BenchmarkAdapter):
    """Minimal adapter that's just valid enough to drive _run_one_cell."""

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
        return CaseScore(case_id=case.case_id, metrics={"a1": 1.0, "grounding": 1.0})

    def metric_schema(self) -> MetricSchema:
        return MetricSchema(
            outcome_metrics=["a1"],
            validity_metrics=["grounding"],
            higher_is_better={"a1": True, "grounding": True},
        )


def _runner(tmp_path: Path) -> BenchmarkRunner:
    config = BenchmarkConfig.model_validate(
        {
            "benchmark": "tiny",
            "modes": ["opensre+llm"],
            "llms": ["claude-4-sonnet"],
            "model_versions": {"claude-4-sonnet": "claude-sonnet-4-5-20250929"},
            "seed": 42,
            "cost_budget_usd": 10.0,
            "output_dir": str(tmp_path / "out"),
        }
    )
    return BenchmarkRunner(config=config, adapter=_TinyAdapter())


def _two_llm_runner(tmp_path: Path) -> tuple[BenchmarkRunner, Path]:
    """Runner over two model arms, so a halt can land between them."""
    out_dir = tmp_path / "out"
    config = BenchmarkConfig.model_validate(
        {
            "benchmark": "tiny",
            "modes": ["opensre+llm"],
            "llms": ["claude-4-sonnet", "gpt-5"],
            "model_versions": {
                "claude-4-sonnet": "claude-sonnet-4-5-20250929",
                "gpt-5": "gpt-5-2025-08-07",
            },
            "seed": 42,
            "cost_budget_usd": 10.0,
            "output_dir": str(out_dir),
            "report_formats": ["json"],
        }
    )
    return BenchmarkRunner(config=config, adapter=_TinyAdapter()), out_dir


def _call_run_one_cell(runner: BenchmarkRunner, tmp_path: Path) -> None:
    """Drive _run_one_cell with a minimal valid arg set."""
    case = BenchmarkCase(case_id="c1", benchmark_name="tiny")
    cases_dir = tmp_path / "cases"
    cases_dir.mkdir(parents=True, exist_ok=True)
    runner._run_one_cell(
        case=case,
        mode="opensre+llm",
        llm="claude-4-sonnet",
        spec=LLM_SPECS["claude-4-sonnet"],
        run_index=0,
        cases_dir=cases_dir,
    )


def _raises_budget(*_args: Any, **_kwargs: Any) -> dict[str, Any]:
    # CostBudgetExceeded takes 3 floats (current, budget, would_add) — not a message string.
    raise CostBudgetExceeded(current_usd=9.50, budget_usd=10.00, would_add_usd=1.50)


def _raises_unknown_model(*_args: Any, **_kwargs: Any) -> dict[str, Any]:
    raise UnknownModel("claude-fake-9000")


def _raises_runtime(*_args: Any, **_kwargs: Any) -> dict[str, Any]:
    raise RuntimeError("tool returned 500")


def _raises_credit_exhausted(*_args: Any, **_kwargs: Any) -> dict[str, Any]:
    raise LLMCreditExhaustedError(
        "OpenAI credit exhausted (provider billing/quota): "
        "top up balance or raise the spending cap at the provider console."
    )


def test_run_one_cell_propagates_llm_credit_exhausted(tmp_path: Path) -> None:
    """LLMCreditExhaustedError must reach the outer handler so the run halts.

    Without this propagation, the bench runner's broad ``except Exception``
    block would catch the credit error and record it as a per-cell failure,
    causing it to grind through hundreds of cells against a dead account
    (the exact regression observed on the June-3 run #2, which burned
    1h42m on zero successful API calls before this halt path existed).
    """
    runner = _runner(tmp_path)
    with (
        patch("tools.investigation.capability.run_investigation", _raises_credit_exhausted),
        pytest.raises(LLMCreditExhaustedError),
    ):
        _call_run_one_cell(runner, tmp_path)


def test_run_one_cell_propagates_cost_budget_exceeded(tmp_path: Path) -> None:
    """CostBudgetExceeded must reach the outer handler so the run halts.

    Greptile flagged this in review: the broad `except Exception` in
    _run_one_cell was catching CostBudgetExceeded and recording it as a
    per-case failure, defeating the budget cap.
    """
    runner = _runner(tmp_path)
    # Patch on the source module — _run_one_cell does a late `from
    # tools.investigation.capability import run_investigation`, which reads the
    # current attribute on that module at call time.
    with (
        patch("tools.investigation.capability.run_investigation", _raises_budget),
        pytest.raises(CostBudgetExceeded),
    ):
        _call_run_one_cell(runner, tmp_path)


def test_run_one_cell_propagates_unknown_model(tmp_path: Path) -> None:
    """UnknownModel is a pre-flight problem (pricing table miss). The runner
    should halt the run rather than mask it as a per-case failure."""
    runner = _runner(tmp_path)
    with (
        patch("tools.investigation.capability.run_investigation", _raises_unknown_model),
        pytest.raises(UnknownModel),
    ):
        _call_run_one_cell(runner, tmp_path)


def test_run_one_cell_catches_other_exceptions_as_cell_failure(tmp_path: Path) -> None:
    """Routine investigation failures (a missing service, a 429 from a tool,
    a malformed alert) should NOT halt the run — only budget/unknown-model
    are run-fatal. One bad cell shouldn't kill a 5,000-cell grid."""
    runner = _runner(tmp_path)
    with patch("tools.investigation.capability.run_investigation", _raises_runtime):
        # Should NOT raise — cell-level failure recorded in the _CellResult
        _call_run_one_cell(runner, tmp_path)


# --------------------------------------------------------------------------- #
# report.json must disclose that a run halted                                 #
# --------------------------------------------------------------------------- #

_INVESTIGATION_OK = {"root_cause": "ok", "report": "ok", "evidence_entries": []}


def _read_report_json(out_dir: Path, run_id: str) -> dict[str, Any]:
    return json.loads((out_dir / run_id / "report.json").read_text(encoding="utf-8"))


def test_report_json_records_a_halted_run(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A run halted part-way through the grid must say so in report.json.

    The exit-code path already refuses to let a halt pass as success
    (``cli.py``: "a halted run that exits 0 is silently lost"), but the exit
    code is not part of the run directory the bench container uploads to S3.
    The pre-registration's ``stopping_rules.partial`` clause turns on this
    distinction: "Numbers from a partial run are NEVER promoted to a
    baseline; only complete runs are baselines", so the artifact has to
    carry it.
    """
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    runner, out_dir = _two_llm_runner(tmp_path)
    with patch(
        "tools.investigation.capability.run_investigation",
        return_value=_INVESTIGATION_OK,
    ):
        outcome = runner.run_without_integrity()

    assert outcome.aborted, "second model arm should not have been reachable"
    report = _read_report_json(out_dir, outcome.report.run_id)
    assert report["aborted"] is True
    assert "OPENAI_API_KEY" in (report["abort_reason"] or "")


def test_report_json_records_a_complete_run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The flip side: a run that finished the grid records that plainly, so
    the field distinguishes the two rather than only ever appearing on
    failure."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    runner, out_dir = _two_llm_runner(tmp_path)
    with patch(
        "tools.investigation.capability.run_investigation",
        return_value=_INVESTIGATION_OK,
    ):
        outcome = runner.run_without_integrity()

    assert not outcome.aborted, outcome.abort_reason
    report = _read_report_json(out_dir, outcome.report.run_id)
    assert report["aborted"] is False
    assert report["abort_reason"] is None
