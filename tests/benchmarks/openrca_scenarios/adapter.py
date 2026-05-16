from __future__ import annotations

import json
from collections.abc import Iterable
from pathlib import Path
from typing import Any

from app.pipeline.runners import run_investigation
from tests.benchmarks._framework.adapters import BenchmarkAdapter, BenchmarkCase

SUITE_DIR = Path(__file__).resolve().parent


class OpenRCAScenariosAdapter(BenchmarkAdapter):
    name = "openrca_scenarios"
    version = "1"

    def load_cases(self, filters: dict[str, Any]) -> Iterable[BenchmarkCase]:
        limit = int(filters.get("limit") or 0)
        for idx, path in enumerate(sorted(SUITE_DIR.rglob("alert.json")), start=1):
            if limit and idx > limit:
                return
            yield BenchmarkCase(
                case_id=str(path.relative_to(SUITE_DIR).parent),
                payload=path,
                tags={"seen_shape": False},
            )

    def run_case(self, case: BenchmarkCase, output_dir: str) -> dict[str, Any]:
        path = Path(case.payload)
        alert = json.loads(path.read_text(encoding="utf-8"))
        final_state = dict(run_investigation(alert))
        payload = {
            "case": {"case_id": case.case_id, "alert_path": str(path)},
            "run": {
                "case_id": case.case_id,
                "final_answer": final_state.get("final_answer") or final_state.get("report"),
                "root_cause": final_state.get("root_cause"),
                "report": final_state.get("report"),
                "steps": [],
                "final_state": final_state,
            },
        }
        output_path = Path(output_dir) / f"{case.case_id.replace('/', '__')}.json"
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
        return payload

    def score_case(self, case: BenchmarkCase, run_result: dict[str, Any]) -> dict[str, Any]:
        has_answer = bool(run_result.get("run", {}).get("final_answer"))
        return {
            "case_id": case.case_id,
            "metrics": {"answer_present": 1.0 if has_answer else 0.0},
            "error": "" if has_answer else "missing_final_answer",
        }

    def metric_schema(self) -> dict[str, Any]:
        return {"primary": "answer_present", "metrics": ["answer_present"]}
