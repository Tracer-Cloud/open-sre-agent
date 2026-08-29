from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from tests.benchmarks.realrca_graph.models import CandidateAnswer

REPO_ROOT = Path(__file__).resolve().parents[3]
BENCH_RESULTS = REPO_ROOT / ".bench-results"
REALRCA_CODEX = BENCH_RESULTS / "realrca-codex"
REALRCA_GRAPH = BENCH_RESULTS / "realrca-graph"
REALRCA_DMA = BENCH_RESULTS / "realrca-dma"
DATASET_DIR = REALRCA_CODEX / "dataset"
DEFAULT_CURRENT_BEST = REALRCA_DMA / "results-test-best8485-gselect-21f8.json"
GRAPH_ROOTS_DIR = REALRCA_GRAPH / "graphs"
DEFAULT_GRAPH_ROOT = GRAPH_ROOTS_DIR / "graph-v3-clusters"

TEST_GRAPH_ROOT_PROFILE = [
    "graph-v482-runtime-jvm-test-probe",
    "graph-v435-custom-monitor-test",
    "graph-v415-tddl-table-metric-test",
    "graph-v401-stale-db-rich-trajectory",
    "graph-v395-trajectory-changed-only",
    "graph-v384-hsf-business-system-error-21f5",
    "graph-v258-21fb-metaq-broker",
    "graph-v255-1d8b-pera-answerseed",
    "graph-v207-unprobed-refresh",
    "graph-v203-related-change-1d8d",
    "graph-v201-21fe-answerseed-changefree",
    "graph-v198-changefree-publish-1d8c",
    "graph-v195-risk-weak-refresh",
    "graph-v191-metaq-app-log-query",
    "graph-v187-rds-sql-detail-test-tddl",
    "graph-v127-test-metric-no-plane-mqcpu",
    "graph-v96-trajectory-v83-on-v11",
    "graph-v92-trajectory-augment",
    "graph-v58-answer-trace-seeds",
    "graph-v11-app-log-signals",
    "graph-v10-sls-sql",
    "graph-v9-access-log",
    "graph-v8-alarm-trace-sql",
    "graph-v7-live-alarm-seed",
    "graph-v6-test-nograph-retry",
    "graph-v5-cpu-gc",
    "graph-v4-refresh-low",
    "graph-v3-clusters",
    "graph-v2-metric-events",
    "graph-v1",
]

VALIDATION_GRAPH_ROOT_PROFILE = [
    "graph-v480-runtime-jvm-c069",
    "graph-v437-custom-monitor-validation",
    "graph-v411-tddl-table-metric-c05d",
    "graph-v380-hsf-business-system-error",
    "graph-v126-metric-no-plane-c073",
    "graph-v125-c073-metaq-refresh",
    "graph-v110-validation-cpu-metaq",
    "graph-v106-validation-full-refresh",
]

GRAPH_ROOT_PROFILES = {
    "latest-test": TEST_GRAPH_ROOT_PROFILE,
    "latest-validation": VALIDATION_GRAPH_ROOT_PROFILE,
}


def load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def graph_roots_for_profile(profile: str) -> list[Path]:
    """Resolve a named local graph-root profile."""
    try:
        names = GRAPH_ROOT_PROFILES[profile]
    except KeyError as exc:
        available = ", ".join(sorted(GRAPH_ROOT_PROFILES))
        raise ValueError(f"unknown graph root profile {profile!r}; available: {available}") from exc
    return [GRAPH_ROOTS_DIR / name for name in names]


def load_cases(split: str, dataset_dir: Path = DATASET_DIR) -> list[dict[str, Any]]:
    if split == "all":
        return load_cases("validation", dataset_dir) + load_cases("test", dataset_dir)
    return load_json(dataset_dir / f"{split}.json")


def rows_by_case(path: Path, source: str | None = None) -> dict[str, CandidateAnswer]:
    raw = load_json(path)
    rows = raw.get("results", []) if isinstance(raw, dict) else raw
    source_name = source or path.stem
    output: dict[str, CandidateAnswer] = {}
    for row in rows:
        if not isinstance(row, dict) or not isinstance(row.get("case_id"), str):
            continue
        case_id = row["case_id"]
        diagnosis = str(row.get("diagnosis_output", "")).strip()
        trace_id = str(row.get("trace_id", "")).strip()
        if diagnosis and trace_id:
            output[case_id] = CandidateAnswer(source_name, case_id, diagnosis, trace_id)
    return output


def graph_context_path(graph_root: Path, split: str, case_id: str) -> Path:
    return graph_root / split / case_id / "graph_context.json"


def realrca_payload_from_rows(
    rows: list[dict[str, str]],
    *,
    split: str,
    model_name: str,
    agent_description: str,
) -> dict[str, Any]:
    return {
        "dataset_version": "V1.0",
        "split": split,
        "submission": {
            "agent_description": agent_description,
            "model": {"name": model_name},
        },
        "results": rows,
    }
