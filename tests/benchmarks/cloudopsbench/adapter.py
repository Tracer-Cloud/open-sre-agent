from __future__ import annotations

import json
from collections.abc import Iterable
from pathlib import Path
from typing import Any

from app.pipeline.runners import run_investigation
from tests.benchmarks._framework.adapters import BenchmarkAdapter, BenchmarkCase
from tests.benchmarks.cloudopsbench.case_loader import (
    BENCHMARK_DIR,
    CloudOpsCase,
    build_alert,
    file_sha256,
    load_cases,
)
from tests.benchmarks.cloudopsbench.replay_backend import CloudOpsBenchReplayBackend
from tests.benchmarks.cloudopsbench.run_suite import _build_resolved_integrations, _json_safe
from tests.benchmarks.cloudopsbench.scoring import score_case


class CloudOpsBenchAdapter(BenchmarkAdapter):
    name = "cloudopsbench"
    version = "1"

    def load_cases(self, filters: dict[str, Any]) -> Iterable[BenchmarkCase]:
        systems = filters.get("systems")
        fault_categories = filters.get("fault_categories") or filters.get("difficulty")
        case_name = filters.get("case") or filters.get("case_name")
        limit = filters.get("limit")

        system_values = systems if isinstance(systems, list) and systems else [None]
        fault_values = (
            fault_categories
            if isinstance(fault_categories, list) and fault_categories
            else [filters.get("fault_category")]
        )
        yielded = 0
        for system in system_values:
            for fault_category in fault_values:
                for case in load_cases(
                    Path(filters.get("benchmark_dir") or BENCHMARK_DIR),
                    system=system,
                    fault_category=fault_category,
                    case_name=case_name,
                    limit=None,
                ):
                    yield BenchmarkCase(
                        case_id=case.case_id,
                        payload=case,
                        tags={"seen_shape": _seen_shape(case)},
                    )
                    yielded += 1
                    if limit and yielded >= int(limit):
                        return

    def run_case(self, case: BenchmarkCase, output_dir: str) -> dict[str, Any]:
        cloudops_case = _cloudops_case(case)
        backend = CloudOpsBenchReplayBackend(cloudops_case)
        final_state = run_investigation(
            build_alert(cloudops_case),
            resolved_integrations=_build_resolved_integrations(cloudops_case, backend),
        )
        final_state_dict = _json_safe(dict(final_state))
        case_data = {
            "case_id": cloudops_case.case_id,
            "system": cloudops_case.system,
            "fault_category": cloudops_case.fault_category,
            "case_name": cloudops_case.case_name,
            "metadata_sha256": file_sha256(cloudops_case.metadata_path),
            "tool_cache_sha256": file_sha256(cloudops_case.tool_cache_path),
            "ground_truth": {
                "fault_taxonomy": cloudops_case.result.fault_taxonomy,
                "fault_object": cloudops_case.result.fault_object,
                "root_cause": cloudops_case.result.root_cause,
            },
            "final_answer": final_state_dict.get("final_answer") or final_state_dict.get("report"),
            "root_cause": final_state_dict.get("root_cause"),
            "report": final_state_dict.get("report"),
            "expert_steps": {
                "path1": list(cloudops_case.process.get("path1") or []),
                "path2": list(cloudops_case.process.get("path2") or []),
            },
            "steps": _steps_from_backend(backend),
            "final_state": final_state_dict,
        }
        payload = {
            "case": {
                "case_id": cloudops_case.case_id,
                "system": cloudops_case.system,
                "fault_category": cloudops_case.fault_category,
                "case_name": cloudops_case.case_name,
                "metadata_path": str(cloudops_case.metadata_path),
                "tool_cache_path": str(cloudops_case.tool_cache_path),
            },
            "run": case_data,
        }
        output_path = Path(output_dir) / f"{cloudops_case.case_name}.json"
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        return payload

    def score_case(self, case: BenchmarkCase, run_result: dict[str, Any]) -> dict[str, Any]:
        payload = score_case(_cloudops_case(case), run_result["run"]).to_dict()
        metrics = payload.get("metrics", {})
        invalid_reasons = payload.get("invalid_reasons", [])
        steps = run_result.get("run", {}).get("steps", [])
        tool_steps = [step for step in steps if isinstance(step, dict) and step.get("action_name")]
        payload["validity_metrics"] = {
            "hallucination_rate": 1.0 if invalid_reasons else 0.0,
            "evidence_support": float(metrics.get("tcr", 0.0)),
            "kubectl_actionability": 1.0 if tool_steps else 0.0,
        }
        return payload

    def metric_schema(self) -> dict[str, Any]:
        return {
            "primary": "a1",
            "metrics": [
                "a1",
                "a3",
                "partial_a1",
                "partial_a3",
                "tcr",
                "exact",
                "in_order",
                "any_order",
                "rel",
                "cov",
                "steps",
                "mtti",
                "iac",
                "rar",
                "ztdr",
            ],
            "validity": ["hallucination_rate", "evidence_support", "kubectl_actionability"],
        }


def _cloudops_case(case: BenchmarkCase) -> CloudOpsCase:
    if not isinstance(case.payload, CloudOpsCase):
        raise TypeError(f"{case.case_id}: expected CloudOpsCase payload")
    return case.payload


def _seen_shape(case: CloudOpsCase) -> bool:
    raw = case.metadata.get("seen_shape")
    if isinstance(raw, bool):
        return raw
    return case.fault_category in {"runtime", "startup", "service"}


def _steps_from_backend(backend: CloudOpsBenchReplayBackend) -> list[dict[str, Any]]:
    steps: list[dict[str, Any]] = []
    for idx, entry in enumerate(backend.action_log, start=1):
        steps.append(
            {
                "step_id": idx,
                "action_type": "tool",
                "action_name": entry.get("action_name"),
                "action_input": entry.get("action_input", {}),
                "error": entry.get("error"),
                "tool_latency": 0.0,
            }
        )
    return steps
