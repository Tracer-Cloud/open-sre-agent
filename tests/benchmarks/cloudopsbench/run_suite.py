from __future__ import annotations

import argparse
import json
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict
from pathlib import Path
from typing import Any

from app.pipeline.runners import run_investigation
from tests.benchmarks.cloudopsbench.case_loader import (
    BENCHMARK_DIR,
    CLOUDOPSBENCH_HF_DATASET_ID,
    CloudOpsCase,
    build_alert,
    file_sha256,
    load_cases,
    validate_corpus,
)
from tests.benchmarks.cloudopsbench.replay_backend import CloudOpsBenchReplayBackend
from tests.benchmarks.cloudopsbench.scoring import CloudOpsCaseScore, score_case, summarize_scores

DEFAULT_OUTPUT_DIR = Path(".cloudopsbench-results")


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run the Cloud-OpsBench RCA suite in OpenSRE.")
    parser.add_argument("--system", default="", help="Filter to boutique or trainticket.")
    parser.add_argument("--fault-category", default="", help="Filter to one fault category.")
    parser.add_argument("--case", default="", help="Filter to one numeric case directory.")
    parser.add_argument("--limit", type=int, default=0, help="Limit cases after sorting/filtering.")
    parser.add_argument("--workers", type=int, default=1, help="Number of case workers.")
    parser.add_argument("--json", action="store_true", help="Print machine-readable JSON results.")
    parser.add_argument(
        "--validate-only", action="store_true", help="Validate downloaded corpus only."
    )
    parser.add_argument(
        "--benchmark-dir",
        default=str(BENCHMARK_DIR),
        help=(
            "Downloaded CloudOpsBench benchmark directory. Run "
            f"`make download-cloudopsbench-hf` to fetch {CLOUDOPSBENCH_HF_DATASET_ID}."
        ),
    )
    parser.add_argument(
        "--output-dir",
        default=str(DEFAULT_OUTPUT_DIR),
        help="Directory for per-case traces and summaries.",
    )
    parser.add_argument(
        "--strict-parity",
        action="store_true",
        help="Exit non-zero when final answers cannot be parsed as CloudOps top-3 JSON.",
    )
    return parser.parse_args(argv)


def _json_safe(value: Any) -> Any:
    try:
        json.dumps(value)
        return value
    except TypeError:
        if isinstance(value, dict):
            return {str(k): _json_safe(v) for k, v in value.items()}
        if isinstance(value, (list, tuple)):
            return [_json_safe(item) for item in value]
        return str(value)


def _build_resolved_integrations(
    case: CloudOpsCase, backend: CloudOpsBenchReplayBackend
) -> dict[str, Any]:
    cluster_name = f"cloudopsbench-{case.system}"
    return {
        "aws": {
            "role_arn": "",
            "external_id": "",
            "region": "us-east-1",
            "cluster_names": [cluster_name],
            "_backend": backend,
        }
    }


def _case_output_path(output_dir: Path, case: CloudOpsCase) -> Path:
    return output_dir / case.system / case.fault_category / f"{case.case_name}.json"


def _action_input_from_step(step: str, namespace: str) -> dict[str, Any]:
    parts = step.split("::")
    action_name = parts[0]
    if action_name == "GetResources":
        return {"resource_type": parts[1] if len(parts) >= 2 else "pods"}
    if action_name == "DescribeResource":
        return {
            "resource_type": parts[1] if len(parts) >= 2 else "pods",
            "name": parts[2] if len(parts) >= 3 else "",
        }
    if action_name in {"GetErrorLogs", "GetRecentLogs"}:
        return {"namespace": namespace, "service_name": parts[1] if len(parts) >= 2 else ""}
    if action_name == "GetServiceDependencies":
        return {"service_name": parts[1] if len(parts) >= 2 else ""}
    if action_name == "GetAppYAML":
        return {"app_name": parts[1] if len(parts) >= 2 else ""}
    if action_name == "CheckServiceConnectivity":
        return {
            "service_name": parts[1] if len(parts) >= 2 else "",
            "port": int(parts[2]) if len(parts) >= 3 and parts[2].isdigit() else 80,
        }
    if action_name == "CheckNodeServiceStatus":
        return {
            "node_name": parts[1] if len(parts) >= 2 else "master",
            "service_name": parts[2] if len(parts) >= 3 else "kube-scheduler",
        }
    return {}


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


def run_case(case: CloudOpsCase, output_dir: Path) -> tuple[dict[str, Any], CloudOpsCaseScore]:
    backend = CloudOpsBenchReplayBackend(case)
    alert = build_alert(case)
    final_state = run_investigation(
        alert_name=f"CloudOpsBench {case.case_id}",
        pipeline_name="cloudopsbench",
        severity="critical",
        raw_alert=alert,
        resolved_integrations=_build_resolved_integrations(case, backend),
    )
    final_state_dict = _json_safe(dict(final_state))
    case_data: dict[str, Any] = {
        "case_id": case.case_id,
        "system": case.system,
        "fault_category": case.fault_category,
        "case_name": case.case_name,
        "metadata_sha256": file_sha256(case.metadata_path),
        "tool_cache_sha256": file_sha256(case.tool_cache_path),
        "ground_truth": {
            "fault_taxonomy": case.result.fault_taxonomy,
            "fault_object": case.result.fault_object,
            "root_cause": case.result.root_cause,
        },
        "final_answer": final_state_dict.get("final_answer") or final_state_dict.get("report"),
        "root_cause": final_state_dict.get("root_cause"),
        "report": final_state_dict.get("report"),
        "expert_steps": {
            "path1": list(case.process.get("path1") or []),
            "path2": list(case.process.get("path2") or []),
        },
        "steps": _steps_from_backend(backend),
        "final_state": final_state_dict,
    }
    score = score_case(case, case_data)
    payload = {
        "case": {
            "case_id": case.case_id,
            "system": case.system,
            "fault_category": case.fault_category,
            "case_name": case.case_name,
            "metadata_path": str(case.metadata_path),
            "tool_cache_path": str(case.tool_cache_path),
        },
        "run": case_data,
        "score": score.to_dict(),
    }

    output_path = _case_output_path(output_dir, case)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return payload, score


def _print_validation_report(report: Any) -> None:
    if report.ok:
        print(f"CloudOpsBench corpus OK: {report.total_cases} cases, {report.file_count} files")
        return
    print("CloudOpsBench corpus validation failed:")
    for error in report.errors:
        print(f"- {error}")


def _run_cases(
    cases: list[CloudOpsCase], output_dir: Path, workers: int
) -> list[CloudOpsCaseScore]:
    scores: list[CloudOpsCaseScore] = []
    if workers <= 1:
        for case in cases:
            _, score = run_case(case, output_dir)
            scores.append(score)
        return scores

    with ThreadPoolExecutor(max_workers=workers) as executor:
        future_to_case = {executor.submit(run_case, case, output_dir): case for case in cases}
        for future in as_completed(future_to_case):
            case = future_to_case[future]
            try:
                _, score = future.result()
            except Exception as exc:  # noqa: BLE001
                raise RuntimeError(f"{case.case_id}: run failed") from exc
            scores.append(score)
    return sorted(scores, key=lambda item: item.case_id)


def run_suite(argv: list[str] | None = None) -> list[CloudOpsCaseScore]:
    args = parse_args(argv)
    output_dir = Path(args.output_dir)
    benchmark_dir = Path(args.benchmark_dir)

    validation_report = validate_corpus(benchmark_dir)
    if args.validate_only:
        _print_validation_report(validation_report)
        if not validation_report.ok:
            raise SystemExit(1)
        return []
    if not validation_report.ok:
        _print_validation_report(validation_report)
        raise SystemExit(1)

    cases = load_cases(
        benchmark_dir,
        system=args.system or None,
        fault_category=args.fault_category or None,
        case_name=args.case or None,
        limit=args.limit or None,
    )
    if not cases:
        raise SystemExit("No CloudOpsBench cases matched the requested filters.")

    scores = _run_cases(cases, output_dir, max(1, int(args.workers)))
    summary = summarize_scores(scores)
    summary_payload = {
        "benchmark_dir": str(benchmark_dir),
        "filters": {
            "system": args.system,
            "fault_category": args.fault_category,
            "case": args.case,
            "limit": args.limit,
        },
        "summary": summary,
        "details": [score.to_dict() for score in scores],
    }
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "summary.json").write_text(
        json.dumps(summary_payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )

    if args.json:
        print(json.dumps(summary_payload, ensure_ascii=False, indent=2))
    else:
        for score in scores:
            metrics = asdict(score.metrics)
            print(
                f"{score.case_id} A@1={metrics['a1']:.0f} A@3={metrics['a3']:.0f} "
                f"TCR={metrics['tcr']:.0f} steps={metrics['steps']:.0f} "
                f"error={score.error or '-'}"
            )
        print(json.dumps(summary["metrics"], ensure_ascii=False, indent=2))

    if args.strict_parity and any(score.error for score in scores):
        raise SystemExit(1)

    return scores


def main(argv: list[str] | None = None) -> int:
    run_suite(argv)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
