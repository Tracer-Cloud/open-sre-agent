"""Rank CloudOpsBench cases by deterministic replay-output size.

This is a cheap pre-flight helper for finding cases likely to pressure the
investigation context window. It does not call an LLM or any remote service.

Usage:
    uv run python infra/scripts/cloudopsbench_size_scan.py --system trainticket --top 20
"""

from __future__ import annotations

import argparse
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from tests.benchmarks.cloudopsbench.case_loader import BENCHMARK_DIR, CloudOpsCase, load_cases
from tests.benchmarks.cloudopsbench.replay_backend import CloudOpsBenchReplayBackend

_TOKENS_PER_CHAR = 0.50
_DEFAULT_TOP = 20


@dataclass(frozen=True)
class CaseSize:
    case_id: str
    process_chars: int
    process_tokens_est: int
    cache_chars: int
    action_count: int
    max_action_chars: int
    largest_action: str
    misses: int


def _json_chars(value: Any) -> int:
    return len(json.dumps(value, default=str, ensure_ascii=False))


def _estimate_tokens(chars: int) -> int:
    return int(chars * _TOKENS_PER_CHAR)


def _steps_for_case(case: CloudOpsCase, paths: tuple[str, ...]) -> list[str]:
    steps: list[str] = []
    for path_name in paths:
        steps.extend(case.process.get(path_name) or [])
    return steps


def _run_step(backend: CloudOpsBenchReplayBackend, step: str) -> tuple[str, dict[str, Any]]:
    parts = step.split("::")
    action = parts[0] if parts else ""

    if action == "GetResources":
        return action, backend.GetResources(parts[1] if len(parts) >= 2 else "pods")
    if action == "DescribeResource":
        return action, backend.DescribeResource(
            parts[1] if len(parts) >= 2 else "services",
            parts[2] if len(parts) >= 3 else "",
        )
    if action == "GetClusterConfiguration":
        return action, backend.GetClusterConfiguration()
    if action == "GetAlerts":
        return action, backend.GetAlerts()
    if action == "GetErrorLogs":
        return action, backend.GetErrorLogs(
            backend.default_namespace,
            parts[1] if len(parts) >= 2 else "",
        )
    if action == "GetRecentLogs":
        return action, backend.GetRecentLogs(
            backend.default_namespace,
            parts[1] if len(parts) >= 2 else "",
        )
    if action == "GetServiceDependencies":
        return action, backend.GetServiceDependencies(parts[1] if len(parts) >= 2 else "")
    if action == "GetAppYAML":
        return action, backend.GetAppYAML(parts[1] if len(parts) >= 2 else "")
    if action == "CheckServiceConnectivity":
        return action, backend.CheckServiceConnectivity(
            parts[1] if len(parts) >= 2 else "",
            int(parts[2]) if len(parts) >= 3 and parts[2].isdigit() else 80,
            backend.default_namespace,
        )
    if action == "CheckNodeServiceStatus":
        return action, backend.CheckNodeServiceStatus(
            parts[1] if len(parts) >= 2 else "master",
            parts[2] if len(parts) >= 3 else "kube-scheduler",
        )

    return action or "<empty>", {"error": f"unsupported process step: {step}"}


def _score_case(case: CloudOpsCase, paths: tuple[str, ...]) -> CaseSize:
    backend = CloudOpsBenchReplayBackend(case)
    action_sizes: list[tuple[int, str]] = []
    outputs: list[dict[str, Any]] = []
    misses = 0

    for step in _steps_for_case(case, paths):
        action, output = _run_step(backend, step)
        chars = _json_chars(output)
        action_sizes.append((chars, action))
        outputs.append(output)
        if output.get("cache_hit") is False:
            misses += 1

    process_chars = _json_chars(outputs)
    cache_chars = _json_chars(backend.tool_cache)
    max_action_chars, largest_action = max(action_sizes, default=(0, ""))

    return CaseSize(
        case_id=case.case_id,
        process_chars=process_chars,
        process_tokens_est=_estimate_tokens(process_chars),
        cache_chars=cache_chars,
        action_count=len(outputs),
        max_action_chars=max_action_chars,
        largest_action=largest_action,
        misses=misses,
    )


def _paths_from_arg(value: str) -> tuple[str, ...]:
    if value == "path1":
        return ("path1",)
    if value == "path2":
        return ("path2",)
    return ("path1", "path2")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--benchmark-dir", type=Path, default=BENCHMARK_DIR)
    parser.add_argument("--system", default="trainticket")
    parser.add_argument("--fault-category", default=None)
    parser.add_argument("--case-name", default=None)
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--top", type=int, default=_DEFAULT_TOP)
    parser.add_argument("--paths", choices=("path1", "path2", "both"), default="both")
    parser.add_argument("--json", action="store_true", help="Emit machine-readable JSON.")
    args = parser.parse_args()

    cases = load_cases(
        args.benchmark_dir,
        system=args.system,
        fault_category=args.fault_category,
        case_name=args.case_name,
        limit=args.limit,
    )
    paths = _paths_from_arg(args.paths)
    rows = sorted((_score_case(case, paths) for case in cases), key=lambda row: row.process_chars)
    rows.reverse()
    selected = rows[: args.top]

    if args.json:
        print(json.dumps([row.__dict__ for row in selected], indent=2))
        return

    print(f"scanned {len(cases)} case(s); showing top {len(selected)} by replayed process size")
    print(
        "process_chars  est_tokens  cache_chars  actions  max_action  largest_action  misses  case_id"
    )
    for row in selected:
        print(
            f"{row.process_chars:13d}  "
            f"{row.process_tokens_est:10d}  "
            f"{row.cache_chars:11d}  "
            f"{row.action_count:7d}  "
            f"{row.max_action_chars:10d}  "
            f"{row.largest_action:14s}  "
            f"{row.misses:6d}  "
            f"{row.case_id}"
        )


if __name__ == "__main__":
    main()
