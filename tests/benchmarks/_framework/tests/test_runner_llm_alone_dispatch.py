"""Tests for the runner's mode-dispatch path between opensre+llm and llm_alone.

The audit's biggest scientific gap was "no in-harness control" — every
opensre claim was being compared against a paper number from a different
harness. The runner's mode dispatch is the load-bearing wire between
the opensre+llm primary mode and the new llm_alone control arm.

These tests pin:

  - llm_alone mode calls ``adapter.build_baseline_tools`` (not
    ``build_opensre_integrations``) — the methods may return the same dict
    for some adapters but they are conceptually separate hooks; bypassing
    the baseline hook would mean a future adapter's per-mode customizations
    silently don't fire.
  - llm_alone mode passes the result of ``baseline_agent_class()`` to
    ``run_investigation`` — same protocol as opensre+llm, different class.
  - When the adapter returns ``None`` from ``baseline_agent_class``, the
    runner refuses an llm_alone config upfront (in _run_inner) instead of
    silently running cells with a None agent.
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
from tests.benchmarks._framework.llm_dispatch import LLM_SPECS
from tests.benchmarks._framework.runner import BenchmarkRunner


class _BaselineAgentStub:
    """Placeholder class — runner only uses the type, never instantiates here."""


class _PureBaselineAgentStub:
    """Same idea as ``_BaselineAgentStub`` but for the third arm."""


class _AdapterWithBaseline(BenchmarkAdapter):
    """Adapter that supports llm_alone via distinct hooks for tracing."""

    name = "with-baseline"
    version = "0.0.1"
    data_contamination_checked = True

    def __init__(self) -> None:
        # Sentinel tracking — the test asserts which method got called
        self.opensre_integrations_calls = 0
        self.baseline_tools_calls = 0

    def load_cases(self, _filters: CaseFilters) -> Iterator[BenchmarkCase]:
        yield BenchmarkCase(case_id="c1", benchmark_name=self.name)

    def build_alert(self, _case: BenchmarkCase) -> AlertPayload:
        return AlertPayload(raw={}, normalized={})

    def build_opensre_integrations(self, _case: BenchmarkCase) -> dict[str, Any]:
        self.opensre_integrations_calls += 1
        return {"_marker": "opensre"}

    def build_baseline_tools(self, _case: BenchmarkCase) -> dict[str, Any]:
        self.baseline_tools_calls += 1
        return {"_marker": "baseline"}

    def score_case(self, case: BenchmarkCase, _run: RunResult, _context: RunContext) -> CaseScore:
        return CaseScore(case_id=case.case_id, metrics={"a1": 1.0})

    def metric_schema(self) -> MetricSchema:
        return MetricSchema(outcome_metrics=["a1"], higher_is_better={"a1": True})

    def investigation_agent_class(self) -> type:
        # Distinct sentinel class so the test can assert which class was
        # actually passed to run_investigation
        return type("_OpensreAgent", (), {})

    def baseline_agent_class(self) -> type[_BaselineAgentStub]:  # type: ignore[override]
        # Stub class doesn't inherit ConnectedInvestigationAgent because the
        # runner only uses the type identity — `agent_class` is threaded
        # through to ``run_investigation`` which is patched out below. The
        # override deliberately violates the base ABC's return type so the
        # test can verify the runner doesn't introspect the class at all.
        return _BaselineAgentStub

    def pure_baseline_agent_class(self) -> type[_PureBaselineAgentStub]:  # type: ignore[override]
        # Same stub-typing rationale as baseline_agent_class above.
        return _PureBaselineAgentStub


class _AdapterWithoutBaseline(BenchmarkAdapter):
    """Adapter that does NOT support llm_alone — baseline_agent_class is None."""

    name = "no-baseline"
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
        return CaseScore(case_id=case.case_id, metrics={"a1": 0.0})

    def metric_schema(self) -> MetricSchema:
        return MetricSchema(outcome_metrics=["a1"], higher_is_better={"a1": True})

    # baseline_agent_class defaults to None (inherited from base)


def _config(tmp_path: Path, modes: list[str]) -> BenchmarkConfig:
    return BenchmarkConfig.model_validate(
        {
            "benchmark": "tiny",
            "modes": modes,
            "llms": ["claude-4-sonnet"],
            "model_versions": {"claude-4-sonnet": "claude-sonnet-4-5-20250929"},
            "seed": 42,
            "cost_budget_usd": 10.0,
            "output_dir": str(tmp_path / "out"),
        }
    )


# --------------------------------------------------------------------------- #
# _run_inner — pre-flight refusal                                             #
# --------------------------------------------------------------------------- #


def test_run_inner_refuses_llm_alone_when_adapter_has_no_baseline(tmp_path: Path) -> None:
    """A config that asks for llm_alone against an adapter without a
    baseline agent must fail at pre-flight with a clear error — never
    silently run cells with a None agent_class."""
    runner = BenchmarkRunner(
        config=_config(tmp_path, modes=["llm_alone"]),
        adapter=_AdapterWithoutBaseline(),
    )
    with pytest.raises(NotImplementedError) as exc_info:
        runner.run_without_integrity()
    assert "llm_alone" in str(exc_info.value)
    assert "no-baseline" in str(exc_info.value)


def test_run_inner_accepts_llm_alone_when_adapter_provides_baseline(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The flip side: same config but an adapter that DOES return a
    baseline_agent_class must proceed past the pre-flight gate. We patch
    run_investigation so the test doesn't depend on the production
    investigation pipeline."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    runner = BenchmarkRunner(
        config=_config(tmp_path, modes=["llm_alone"]),
        adapter=_AdapterWithBaseline(),
    )
    with patch(
        "core.orchestration.entrypoints.run_investigation",
        return_value={"root_cause": "ok", "report": "ok", "evidence_entries": []},
    ):
        outcome = runner.run_without_integrity()
    assert not outcome.aborted, outcome.abort_reason


# --------------------------------------------------------------------------- #
# _run_one_cell — per-mode dispatch                                           #
# --------------------------------------------------------------------------- #


def test_run_one_cell_llm_alone_calls_build_baseline_tools(tmp_path: Path) -> None:
    """The baseline path must use the adapter's baseline_tools hook, not
    the opensre integrations hook. The two methods may return the same
    dict for adapters where the tool surface matches, but the runner has
    to call the right one so per-mode adapter customizations stay
    selectable."""
    adapter = _AdapterWithBaseline()
    runner = BenchmarkRunner(config=_config(tmp_path, modes=["llm_alone"]), adapter=adapter)
    cases_dir = tmp_path / "cases"
    cases_dir.mkdir(parents=True)
    with patch(
        "core.orchestration.entrypoints.run_investigation",
        return_value={"root_cause": "x", "report": "x", "evidence_entries": []},
    ):
        runner._run_one_cell(
            case=BenchmarkCase(case_id="c1", benchmark_name="with-baseline"),
            mode="llm_alone",
            llm="claude-4-sonnet",
            spec=LLM_SPECS["claude-4-sonnet"],
            run_index=0,
            cases_dir=cases_dir,
        )
    assert adapter.baseline_tools_calls == 1
    assert adapter.opensre_integrations_calls == 0


def test_run_one_cell_opensre_mode_calls_build_opensre_integrations(tmp_path: Path) -> None:
    """Symmetric: the opensre+llm path must hit the opensre hook, not the
    baseline one. Confirms the dispatch isn't inverted."""
    adapter = _AdapterWithBaseline()
    runner = BenchmarkRunner(config=_config(tmp_path, modes=["opensre+llm"]), adapter=adapter)
    cases_dir = tmp_path / "cases"
    cases_dir.mkdir(parents=True)
    with patch(
        "core.orchestration.entrypoints.run_investigation",
        return_value={"root_cause": "x", "report": "x", "evidence_entries": []},
    ):
        runner._run_one_cell(
            case=BenchmarkCase(case_id="c1", benchmark_name="with-baseline"),
            mode="opensre+llm",
            llm="claude-4-sonnet",
            spec=LLM_SPECS["claude-4-sonnet"],
            run_index=0,
            cases_dir=cases_dir,
        )
    assert adapter.opensre_integrations_calls == 1
    assert adapter.baseline_tools_calls == 0


def test_run_one_cell_passes_baseline_agent_class_when_llm_alone(tmp_path: Path) -> None:
    """The agent_class threaded into run_investigation must come from
    baseline_agent_class on llm_alone cells. Otherwise the cell silently
    runs through opensre's default agent and we'd measure the wrong
    thing."""
    adapter = _AdapterWithBaseline()
    runner = BenchmarkRunner(config=_config(tmp_path, modes=["llm_alone"]), adapter=adapter)
    cases_dir = tmp_path / "cases"
    cases_dir.mkdir(parents=True)
    captured: dict[str, Any] = {}

    def _capture_kwargs(*_args: Any, **kwargs: Any) -> dict[str, Any]:
        captured.update(kwargs)
        return {"root_cause": "x", "report": "x", "evidence_entries": []}

    with patch("core.orchestration.entrypoints.run_investigation", _capture_kwargs):
        runner._run_one_cell(
            case=BenchmarkCase(case_id="c1", benchmark_name="with-baseline"),
            mode="llm_alone",
            llm="claude-4-sonnet",
            spec=LLM_SPECS["claude-4-sonnet"],
            run_index=0,
            cases_dir=cases_dir,
        )
    assert captured["agent_class"] is _BaselineAgentStub
    # And the baseline marker integrations made it through too
    assert captured["resolved_integrations"]["_marker"] == "baseline"


# --------------------------------------------------------------------------- #
# llm_alone_pure — third arm (pure baseline)                                  #
# --------------------------------------------------------------------------- #


def test_run_inner_refuses_llm_alone_pure_when_adapter_has_no_pure_baseline(
    tmp_path: Path,
) -> None:
    """Mirror of the llm_alone refusal: a config asking for llm_alone_pure
    against an adapter that returns None from pure_baseline_agent_class
    must fail at pre-flight, not silently run with None agent."""
    runner = BenchmarkRunner(
        config=_config(tmp_path, modes=["llm_alone_pure"]),
        adapter=_AdapterWithoutBaseline(),
    )
    with pytest.raises(NotImplementedError) as exc_info:
        runner.run_without_integrity()
    assert "llm_alone_pure" in str(exc_info.value) or "pure baseline" in str(exc_info.value)
    assert "no-baseline" in str(exc_info.value)


def test_run_one_cell_llm_alone_pure_uses_baseline_tools_and_pure_agent(
    tmp_path: Path,
) -> None:
    """llm_alone_pure uses the SAME tool surface as llm_alone (both go
    through build_baseline_tools — the methodological constant across
    all three modes is the per-case tool inventory). What differs is the
    agent class: pure_baseline_agent_class for llm_alone_pure."""
    adapter = _AdapterWithBaseline()
    runner = BenchmarkRunner(config=_config(tmp_path, modes=["llm_alone_pure"]), adapter=adapter)
    cases_dir = tmp_path / "cases"
    cases_dir.mkdir(parents=True)
    captured: dict[str, Any] = {}

    def _capture_kwargs(*_args: Any, **kwargs: Any) -> dict[str, Any]:
        captured.update(kwargs)
        return {"root_cause": "x", "report": "x", "evidence_entries": []}

    with patch("core.orchestration.entrypoints.run_investigation", _capture_kwargs):
        runner._run_one_cell(
            case=BenchmarkCase(case_id="c1", benchmark_name="with-baseline"),
            mode="llm_alone_pure",
            llm="claude-4-sonnet",
            spec=LLM_SPECS["claude-4-sonnet"],
            run_index=0,
            cases_dir=cases_dir,
        )
    # Pure baseline uses the baseline-tools path (same as llm_alone)
    assert adapter.baseline_tools_calls == 1
    assert adapter.opensre_integrations_calls == 0
    # But threads through the PURE agent class, not the regular baseline one
    assert captured["agent_class"] is _PureBaselineAgentStub
    assert captured["agent_class"] is not _BaselineAgentStub
    # Integrations dict still carries the baseline marker
    assert captured["resolved_integrations"]["_marker"] == "baseline"
