from __future__ import annotations

import os
from collections.abc import Iterable
from typing import Any

from tests.benchmarks._framework.adapters import BenchmarkAdapter, BenchmarkCase
from tests.benchmarks._framework.config import BenchmarkConfig
from tests.benchmarks._framework.runner import run_benchmark


class FakeAdapter(BenchmarkAdapter):
    name = "fake"
    version = "1"

    def load_cases(self, _filters: dict[str, Any]) -> Iterable[BenchmarkCase]:
        yield BenchmarkCase("case-1", {"id": 1}, {"seen_shape": True})

    def run_case(self, case: BenchmarkCase, _output_dir: str) -> dict[str, Any]:
        return {
            "run": {
                "case_id": case.case_id,
                "steps": [{"action_name": "GetResources"}],
                "final_state": {
                    "tokens_by_model": {"gpt-5": {"input_tokens": 1000, "output_tokens": 1000}}
                },
            }
        }

    def score_case(self, case: BenchmarkCase, _run_result: dict[str, Any]) -> dict[str, Any]:
        return {"case_id": case.case_id, "metrics": {"a1": 1.0}, "error": ""}

    def metric_schema(self) -> dict[str, Any]:
        return {"primary": "a1", "metrics": ["a1"]}


class TwoCaseAdapter(FakeAdapter):
    def __init__(self) -> None:
        self.seen_llms: list[str | None] = []

    def load_cases(self, _filters: dict[str, Any]) -> Iterable[BenchmarkCase]:
        yield BenchmarkCase("case-1", {"id": 1}, {"seen_shape": True})
        yield BenchmarkCase("case-2", {"id": 2}, {"seen_shape": False})

    def run_case(self, case: BenchmarkCase, output_dir: str) -> dict[str, Any]:
        self.seen_llms.append(os.environ.get("OPENSRE_BENCH_LLM"))
        return super().run_case(case, output_dir)


def test_run_benchmark_writes_reports_and_cost(tmp_path) -> None:
    result = run_benchmark(
        FakeAdapter(),
        BenchmarkConfig(
            benchmark="fake",
            llms=("gpt-5",),
            output_dir=str(tmp_path),
            workers=1,
        ),
    )

    assert result.payload["case_count"] == 1
    assert result.payload["results"][0]["seen_shape"] is True
    assert result.payload["cost_usd"] > 0
    assert (tmp_path / "summary.json").is_file()


def test_run_benchmark_pins_llm_env_once_for_parallel_batch(tmp_path) -> None:
    adapter = TwoCaseAdapter()

    result = run_benchmark(
        adapter,
        BenchmarkConfig(
            benchmark="fake",
            llms=("gpt-5",),
            output_dir=str(tmp_path),
            workers=2,
            strict_parity=True,
        ),
    )

    assert adapter.seen_llms == ["gpt-5", "gpt-5"]
    assert result.payload["workers"] == 2
    assert result.payload["strict_parity"] is True


def test_run_benchmark_uses_one_worker_when_budget_is_set(tmp_path) -> None:
    result = run_benchmark(
        TwoCaseAdapter(),
        BenchmarkConfig(
            benchmark="fake",
            llms=("gpt-5",),
            output_dir=str(tmp_path),
            workers=8,
            cost_budget_usd=1.0,
        ),
    )

    assert result.payload["workers"] == 1
    assert result.payload["requested_workers"] == 8


def test_run_benchmark_emits_requested_modes(tmp_path) -> None:
    result = run_benchmark(
        FakeAdapter(),
        BenchmarkConfig(
            benchmark="fake",
            modes=("opensre+llm", "llm_alone"),
            llms=("gpt-5",),
            output_dir=str(tmp_path),
            workers=1,
            strict_parity=True,
        ),
    )

    assert result.payload["modes"] == ["opensre+llm", "llm_alone"]
    assert [row["mode"] for row in result.payload["results"]] == [
        "opensre+llm",
        "llm_alone",
    ]
