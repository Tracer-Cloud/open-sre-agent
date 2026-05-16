from __future__ import annotations

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
