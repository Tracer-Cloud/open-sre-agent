from __future__ import annotations

import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from tests.benchmarks._framework.adapters import BenchmarkAdapter, BenchmarkCase
from tests.benchmarks._framework.config import BenchmarkConfig
from tests.benchmarks._framework.cost import estimate_case_cost_usd, tokens_by_model_from_state
from tests.benchmarks._framework.llm_dispatch import llm_environment
from tests.benchmarks._framework.reporting import write_reports


@dataclass(frozen=True)
class BenchmarkRunResult:
    payload: dict[str, Any]
    output_dir: Path


def _case_seen_shape(case: BenchmarkCase) -> bool:
    raw = case.tags.get("seen_shape")
    return bool(raw) if raw is not None else False


def _run_one(
    adapter: BenchmarkAdapter,
    case: BenchmarkCase,
    *,
    llm: str,
    run_index: int,
    output_dir: Path,
) -> dict[str, Any]:
    started = time.perf_counter()
    case_output = output_dir / "cases" / llm / f"run-{run_index}"
    run_payload = adapter.run_case(case, str(case_output))
    score = adapter.score_case(case, run_payload)
    final_state = run_payload.get("run", {}).get("final_state", {})
    tokens_by_model = tokens_by_model_from_state(
        final_state if isinstance(final_state, dict) else {}
    )
    cost_usd, cost_breakdown = estimate_case_cost_usd(tokens_by_model, llm=llm)
    return {
        "benchmark": adapter.name,
        "adapter_version": adapter.version,
        "mode": "opensre+llm",
        "llm": llm,
        "run_index": run_index,
        "case_id": case.case_id,
        "seen_shape": _case_seen_shape(case),
        "duration_seconds": time.perf_counter() - started,
        "cost_usd": cost_usd,
        "cost_breakdown_usd": cost_breakdown,
        "decision_trace": run_payload.get("run", {}).get("steps", []),
        "run": run_payload.get("run", {}),
        "score": score,
    }


def run_benchmark(adapter: BenchmarkAdapter, config: BenchmarkConfig) -> BenchmarkRunResult:
    output_dir = Path(config.output_dir)
    llms = config.llms or ("default",)
    cases = list(adapter.load_cases(config.filters))
    if not cases:
        raise RuntimeError("No benchmark cases matched the requested filters.")

    results: list[dict[str, Any]] = []
    total_cost = 0.0

    def record(result: dict[str, Any]) -> None:
        nonlocal total_cost
        total_cost += float(result.get("cost_usd") or 0.0)
        if config.cost_budget_usd is not None and total_cost > config.cost_budget_usd:
            raise RuntimeError(
                f"Cost budget exceeded: ${total_cost:.4f} > ${config.cost_budget_usd:.4f}"
            )
        results.append(result)

    requested_workers = max(1, int(config.workers))
    workers = 1 if config.cost_budget_usd is not None else requested_workers
    for llm in llms:
        jobs = [
            (case, run_index) for case in cases for run_index in range(1, config.runs_per_case + 1)
        ]
        with llm_environment(llm):
            if workers == 1:
                for case, run_index in jobs:
                    record(
                        _run_one(adapter, case, llm=llm, run_index=run_index, output_dir=output_dir)
                    )
                continue
            with ThreadPoolExecutor(max_workers=workers) as executor:
                futures = [
                    executor.submit(
                        _run_one,
                        adapter,
                        case,
                        llm=llm,
                        run_index=run_index,
                        output_dir=output_dir,
                    )
                    for case, run_index in jobs
                ]
                for future in as_completed(futures):
                    record(future.result())

    results.sort(key=lambda row: (row["llm"], row["case_id"], row["run_index"]))
    if config.strict_parity:
        _validate_strict_parity(
            results,
            llms=llms,
            cases=cases,
            runs_per_case=config.runs_per_case,
        )
    payload = {
        "benchmark": adapter.name,
        "adapter_version": adapter.version,
        "case_count": len(cases),
        "llms": list(llms),
        "runs_per_case": config.runs_per_case,
        "workers": workers,
        "requested_workers": requested_workers,
        "strict_parity": config.strict_parity,
        "filters": config.filters,
        "metric_schema": adapter.metric_schema(),
        "cost_usd": total_cost,
        "results": results,
    }
    write_reports(payload, output_dir, config.report_formats)
    return BenchmarkRunResult(payload=payload, output_dir=output_dir)


def _validate_strict_parity(
    results: list[dict[str, Any]],
    *,
    llms: tuple[str, ...],
    cases: list[BenchmarkCase],
    runs_per_case: int,
) -> None:
    expected = {
        (llm, case.case_id, run_index)
        for llm in llms
        for case in cases
        for run_index in range(1, runs_per_case + 1)
    }
    observed = {
        (str(row.get("llm")), str(row.get("case_id")), int(row.get("run_index", 0)))
        for row in results
    }
    if observed != expected or len(results) != len(expected):
        missing = sorted(expected - observed)
        extra = sorted(observed - expected)
        raise RuntimeError(
            "Strict parity failed: "
            f"missing={missing[:5]} extra={extra[:5]} "
            f"expected={len(expected)} observed={len(observed)}"
        )
