from __future__ import annotations

import argparse
import concurrent.futures
import glob
import json
import os
import subprocess
import time
import uuid
from pathlib import Path
from typing import Any

from tests.benchmarks.realrca_graph.answer_outliers import (
    build_answer_outlier_report,
    render_answer_outlier_markdown,
)
from tests.benchmarks.realrca_graph.boundary_analysis import (
    build_boundary_delta_report,
    render_boundary_delta_markdown,
)
from tests.benchmarks.realrca_graph.bundle import build_evidence_bundle
from tests.benchmarks.realrca_graph.bundle_cache import build_evidence_bundle_cached
from tests.benchmarks.realrca_graph.calibration import (
    build_calibration_report,
    render_calibration_markdown,
)
from tests.benchmarks.realrca_graph.case_analogues import (
    build_case_analogue_report,
    render_case_analogue_markdown,
)
from tests.benchmarks.realrca_graph.causal_paths import (
    build_causal_path_report,
    render_causal_path_markdown,
)
from tests.benchmarks.realrca_graph.contract_gaps import (
    build_contract_gap_report,
    render_contract_gap_markdown,
)
from tests.benchmarks.realrca_graph.coverage_gaps import (
    build_coverage_gap_report,
    render_coverage_gap_markdown,
)
from tests.benchmarks.realrca_graph.enrichment import enrich_answer, terms_from_audit_case
from tests.benchmarks.realrca_graph.frontier import (
    build_frontier_report,
    render_frontier_markdown,
)
from tests.benchmarks.realrca_graph.generation import (
    build_generation_package,
    candidate_row_from_result,
    extract_candidate_result,
    render_generation_prompt,
    sanitize_visible_tool_signals,
    validate_candidate_result,
)
from tests.benchmarks.realrca_graph.graph_analogues import (
    build_graph_analogue_report,
    graph_analogues_for_prompt_payload,
    render_graph_analogue_markdown,
)
from tests.benchmarks.realrca_graph.graph_store import (
    DEFAULT_GRAPH_DB,
    index_graph_roots,
    index_resolved_graphs,
    search_nodes,
)
from tests.benchmarks.realrca_graph.io import (
    DATASET_DIR,
    DEFAULT_CURRENT_BEST,
    DEFAULT_GRAPH_ROOT,
    GRAPH_ROOT_PROFILES,
    REALRCA_DMA,
    graph_context_path,
    graph_roots_for_profile,
    load_cases,
    load_json,
    realrca_payload_from_rows,
    rows_by_case,
    write_json,
)
from tests.benchmarks.realrca_graph.llm_verifier import (
    build_pairwise_verifier_package,
    extract_pairwise_verifier_result,
    parse_pairwise_verifier_decision,
    render_pairwise_verifier_prompt,
    should_accept_pairwise_decision,
)
from tests.benchmarks.realrca_graph.models import CandidateAnswer, CandidateDecision
from tests.benchmarks.realrca_graph.path_frontier import (
    build_path_frontier_report,
    render_path_frontier_markdown,
)
from tests.benchmarks.realrca_graph.pipeline import (
    build_pipeline_status,
    render_pipeline_status_markdown,
)
from tests.benchmarks.realrca_graph.probe_feedback import ProbeFeedbackLedger
from tests.benchmarks.realrca_graph.raw_inventory import (
    build_raw_inventory_report,
    render_raw_inventory_markdown,
)
from tests.benchmarks.realrca_graph.reports import build_triage_report, render_triage_markdown
from tests.benchmarks.realrca_graph.score_boundaries import (
    build_score_boundary_report,
    render_score_boundary_markdown,
)
from tests.benchmarks.realrca_graph.score_tomography import (
    build_tomography_report,
    render_tomography_markdown,
)
from tests.benchmarks.realrca_graph.selector_calibration import (
    build_selector_calibration_report,
    render_selector_calibration_markdown,
)
from tests.benchmarks.realrca_graph.synthesis import synthesize_answer
from tests.benchmarks.realrca_graph.trace_repair import repair_trace_id
from tests.benchmarks.realrca_graph.trajectory_evidence import augment_graph_context_with_trajectory
from tests.benchmarks.realrca_graph.validation import (
    score_validation_file,
    write_validation_summary,
)
from tests.benchmarks.realrca_graph.validation_memory import (
    DEFAULT_VALIDATION_MEMORY,
    load_validation_memory,
    match_validation_exemplars,
)
from tests.benchmarks.realrca_graph.verifier import decide_candidate, score_candidate

DMA_BIN = Path("/Users/shili/.local/bin/dma")
TERMINAL_STATUSES = {"completed", "failed", "cancelled", "canceled", "errored", "error"}


def _source_name(path: Path) -> str:
    return path.stem


def _candidate_pool(paths: list[Path]) -> dict[str, list[CandidateAnswer]]:
    candidates_by_case: dict[str, list[CandidateAnswer]] = {}
    seen_by_case: dict[str, set[tuple[str, str]]] = {}
    for path in paths:
        for case_id, row in rows_by_case(path, source=_source_name(path)).items():
            key = (row.diagnosis_output, row.trace_id)
            seen = seen_by_case.setdefault(case_id, set())
            if key in seen:
                continue
            seen.add(key)
            candidates_by_case.setdefault(case_id, []).append(row)
    return candidates_by_case


def _candidate_paths(args: argparse.Namespace) -> list[Path]:
    requested: list[Path] = list(getattr(args, "candidate", []))
    for pattern in getattr(args, "candidate_glob", []):
        requested.extend(Path(match) for match in sorted(glob.glob(str(pattern), recursive=True)))

    paths: list[Path] = []
    seen: set[str] = set()
    for path in requested:
        if not path.exists():
            continue
        key = str(path.resolve())
        if key in seen:
            continue
        seen.add(key)
        paths.append(path)
    return paths


def _baseline_order(baseline_path: Path) -> list[str]:
    payload = load_json(baseline_path)
    return [
        str(row["case_id"])
        for row in payload.get("results", [])
        if isinstance(row, dict) and isinstance(row.get("case_id"), str)
    ]


def _graph_roots(args: argparse.Namespace) -> list[Path]:
    roots = list(getattr(args, "graph_root", []))
    for profile in getattr(args, "graph_profile", []):
        roots.extend(graph_roots_for_profile(profile))
    return roots or [DEFAULT_GRAPH_ROOT]


def _add_graph_root_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--graph-root", type=Path, action="append", default=[])
    parser.add_argument(
        "--graph-profile",
        action="append",
        choices=sorted(GRAPH_ROOT_PROFILES),
        default=[],
        help=(
            "Append a named graph-root profile after explicit --graph-root values. "
            "Use latest-test for hidden-test iteration or latest-validation for public "
            "validation calibration."
        ),
    )


def _add_candidate_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--candidate", type=Path, action="append", default=[])
    parser.add_argument(
        "--candidate-glob",
        action="append",
        default=[],
        help=(
            "Append candidate result files matched by a glob pattern. Repeat the flag "
            "to scan several historical candidate pools; recursive ** patterns are supported."
        ),
    )


def _find_graph_context_path(graph_roots: list[Path], split: str, case_id: str) -> Path | None:
    for root in graph_roots:
        path = graph_context_path(root, split, case_id)
        if path.exists():
            return path
    return None


def _selected_case_ids(requested: list[str], ordered_case_ids: list[str]) -> set[str]:
    if not requested:
        return set(ordered_case_ids)

    selected: set[str] = set()
    for value in requested:
        matches = [
            case_id for case_id in ordered_case_ids if case_id == value or case_id.endswith(value)
        ]
        selected.update(matches or [value])
    return selected


def _case_suffix(case_id: str) -> str:
    return case_id.rsplit("-", 1)[-1][-4:]


def _probe_suffix(agent_name: str) -> str:
    for token in reversed(agent_name.lower().split("-")):
        if len(token) == 5 and token.startswith("321"):
            return token[-4:]
        if len(token) == 4 and all(char in "0123456789abcdef" for char in token):
            return token
    return ""


def _probed_suffixes(leaderboard_path: Path | None, team_name: str) -> set[str]:
    if leaderboard_path is None or not leaderboard_path.exists():
        return set()
    payload = load_json(leaderboard_path)
    output: set[str] = set()
    for item in payload.get("items", []) if isinstance(payload, dict) else []:
        if not isinstance(item, dict) or item.get("team_name") != team_name:
            continue
        suffix = _probe_suffix(str(item.get("agent_name") or ""))
        if suffix:
            output.add(suffix)
    return output


def _probe_agents_by_suffix(
    leaderboard_path: Path | None,
    team_name: str,
) -> dict[str, list[str]]:
    if leaderboard_path is None or not leaderboard_path.exists():
        return {}
    payload = load_json(leaderboard_path)
    output: dict[str, list[str]] = {}
    for item in payload.get("items", []) if isinstance(payload, dict) else []:
        if not isinstance(item, dict) or item.get("team_name") != team_name:
            continue
        agent_name = str(item.get("agent_name") or "")
        suffix = _probe_suffix(agent_name)
        if suffix and agent_name:
            output.setdefault(suffix, []).append(agent_name)
    return output


def _case_meta_by_id(split: str, dataset_dir: Path) -> dict[str, dict[str, Any]]:
    return {
        str(item["case_id"]): item
        for item in load_cases(split, dataset_dir)
        if isinstance(item, dict) and isinstance(item.get("case_id"), str)
    }


def _snapshot_ref(case: dict[str, Any]) -> str:
    value = case.get("snapshot_id") or case.get("data_ref")
    return str(value or "").strip()


def _trajectory_signals_by_case(
    paths: list[Path],
    *,
    limit: int,
) -> dict[str, list[dict[str, Any]]]:
    output: dict[str, list[dict[str, Any]]] = {}
    for path in paths:
        if not path.exists():
            continue
        payload = load_json(path)
        for item in payload.get("results", []) if isinstance(payload, dict) else []:
            if not isinstance(item, dict) or not isinstance(item.get("case_id"), str):
                continue
            signals = sanitize_visible_tool_signals(
                item.get("missing_terms") or [],
                limit=limit,
            )
            if not signals:
                continue
            existing = output.setdefault(item["case_id"], [])
            existing.extend(signals)
            output[item["case_id"]] = sorted(
                existing,
                key=lambda signal: (
                    -int(signal.get("score") or 0),
                    str(signal.get("kind") or ""),
                    str(signal.get("term") or ""),
                ),
            )[:limit]
    return output


def _frontier_context_by_case(path: Path | None) -> dict[str, dict[str, Any]]:
    if path is None or not path.exists():
        return {}
    payload = load_json(path)
    output: dict[str, dict[str, Any]] = {}
    for item in payload.get("cases", []) if isinstance(payload, dict) else []:
        if not isinstance(item, dict):
            continue
        case_id = str(item.get("case_id") or "")
        suffix = str(item.get("case_suffix") or "")
        if case_id:
            output[case_id] = item
        if suffix:
            output[suffix] = item
    return output


def _graph_analogue_context_by_case(path: Path | None) -> dict[str, list[dict[str, Any]]]:
    if path is None or not path.exists():
        return {}
    payload = load_json(path)
    if not isinstance(payload, dict):
        return {}
    return graph_analogues_for_prompt_payload(payload)


def build_bundle_command(args: argparse.Namespace) -> int:
    graph_context = load_json(args.graph)
    bundle = build_evidence_bundle(
        graph_context,
        evidence_limit=args.evidence_limit,
        hypothesis_limit=args.hypothesis_limit,
        support_limit=args.support_limit,
    )
    payload = bundle.to_dict()
    if args.out:
        write_json(args.out, payload)
    else:
        print(payload)
    return 0


def causal_paths_command(args: argparse.Namespace) -> int:
    graph_context = load_json(args.graph)
    bundle = build_evidence_bundle(
        graph_context,
        evidence_limit=args.evidence_limit,
        hypothesis_limit=args.hypothesis_limit,
        support_limit=args.support_limit,
    )
    report = build_causal_path_report(
        graph_context,
        bundle,
        max_depth=args.max_depth,
        seed_limit=args.seed_limit,
    )
    write_json(args.out_json, report.to_dict())
    if args.out_md:
        args.out_md.parent.mkdir(parents=True, exist_ok=True)
        args.out_md.write_text(
            render_causal_path_markdown(report, limit=args.markdown_limit),
            encoding="utf-8",
        )
    print(
        f"causal paths case={report.case_id} hypotheses={report.hypothesis_count} "
        f"wrote {args.out_json}"
    )
    return 0


def _decision_for_case(
    *,
    case_id: str,
    baseline: CandidateAnswer,
    graph_path: Path,
    split: str,
    candidates_by_case: dict[str, list[CandidateAnswer]],
    args: argparse.Namespace,
    feedback_ledger: ProbeFeedbackLedger | None = None,
) -> CandidateDecision:
    bundle = build_evidence_bundle_cached(
        graph_path,
        evidence_limit=args.evidence_limit,
        hypothesis_limit=args.hypothesis_limit,
        support_limit=args.support_limit,
    )
    candidates = candidates_by_case.get(case_id, [])
    return decide_candidate(
        baseline,
        candidates,
        bundle,
        min_support=args.min_support,
        min_margin=args.min_margin,
        min_modalities=args.min_modalities,
        max_novelty=args.max_novelty,
        probe_feedback=feedback_ledger.for_case_id(case_id)
        if feedback_ledger is not None
        else None,
    )


def _feedback_ledger(leaderboard_path: Path | None, team_name: str) -> ProbeFeedbackLedger | None:
    if leaderboard_path is None or not leaderboard_path.exists():
        return None
    payload = load_json(leaderboard_path)
    if not isinstance(payload, dict):
        return None
    return ProbeFeedbackLedger.from_leaderboard(payload, team_name=team_name)


def select_command(args: argparse.Namespace) -> int:
    baseline_rows = rows_by_case(args.baseline, source=_source_name(args.baseline))
    candidate_paths = _candidate_paths(args)
    candidates_by_case = _candidate_pool(candidate_paths)
    ordered_case_ids = _baseline_order(args.baseline)
    selected_case_ids = _selected_case_ids(args.case_id, ordered_case_ids)
    graph_roots = _graph_roots(args)
    probed_suffixes = (
        _probed_suffixes(args.leaderboard, args.team_name) if args.skip_probed_cases else set()
    )
    feedback_ledger = _feedback_ledger(args.leaderboard, args.team_name)
    decisions: list[CandidateDecision] = []
    rows: list[dict[str, str]] = []
    missing_graphs: list[str] = []
    skipped_probed_case_ids: list[str] = []

    for case_id in ordered_case_ids:
        baseline = baseline_rows[case_id]
        if case_id not in selected_case_ids:
            rows.append(baseline.to_result_row())
            continue
        if _case_suffix(case_id) in probed_suffixes:
            rows.append(baseline.to_result_row())
            skipped_probed_case_ids.append(case_id)
            continue
        graph_path = _find_graph_context_path(graph_roots, args.split, case_id)
        if graph_path is None:
            missing_graphs.append(case_id)
            rows.append(baseline.to_result_row())
            continue
        decision = _decision_for_case(
            case_id=case_id,
            baseline=baseline,
            graph_path=graph_path,
            split=args.split,
            candidates_by_case=candidates_by_case,
            args=args,
            feedback_ledger=feedback_ledger,
        )
        decisions.append(decision)
        rows.append(decision.selected.to_result_row())

    if missing_graphs and args.fail_on_missing_graph:
        joined = ", ".join(missing_graphs[:8])
        raise FileNotFoundError(f"missing graph contexts for {len(missing_graphs)} cases: {joined}")

    result_payload = realrca_payload_from_rows(
        rows,
        split=args.split,
        model_name=args.model_name,
        agent_description=args.agent_description,
    )
    audit: dict[str, Any] = {
        "baseline": str(args.baseline),
        "graph_root": str(graph_roots[0]) if graph_roots else "",
        "graph_roots": [str(root) for root in graph_roots],
        "candidate_files": [str(path) for path in candidate_paths],
        "case_count": len(rows),
        "selected_case_count": len(decisions),
        "skip_probed_cases": bool(args.skip_probed_cases),
        "probed_suffix_count": len(probed_suffixes),
        "skipped_probed_case_ids": skipped_probed_case_ids,
        "accepted_replacements": [
            decision.case_id for decision in decisions if decision.accepted_replacement
        ],
        "missing_graphs": missing_graphs,
        "decisions": [decision.to_dict() for decision in decisions],
    }
    write_json(args.out_result, result_payload)
    write_json(args.out_audit, audit)
    print(
        f"wrote {args.out_result} rows={len(rows)} "
        f"accepted={len(audit['accepted_replacements'])} missing_graphs={len(missing_graphs)}"
    )
    return 0


def repair_traces_command(args: argparse.Namespace) -> int:
    baseline_rows = rows_by_case(args.baseline, source=_source_name(args.baseline))
    ordered_case_ids = _baseline_order(args.baseline)
    selected_case_ids = _selected_case_ids(args.case_id, ordered_case_ids)
    graph_roots = _graph_roots(args)
    rows: list[dict[str, str]] = []
    repairs: list[dict[str, Any]] = []
    missing_graphs: list[str] = []

    for case_id in ordered_case_ids:
        baseline = baseline_rows[case_id]
        if case_id not in selected_case_ids:
            rows.append(baseline.to_result_row())
            continue

        graph_path = _find_graph_context_path(graph_roots, args.split, case_id)
        if graph_path is None:
            missing_graphs.append(case_id)
            rows.append(baseline.to_result_row())
            repairs.append(
                {
                    "case_id": case_id,
                    "graph_path": None,
                    "old_trace_id": baseline.trace_id,
                    "new_trace_id": baseline.trace_id,
                    "repaired": False,
                    "reason": "missing_graph_context",
                }
            )
            continue

        repaired = repair_trace_id(
            baseline,
            load_json(graph_path),
            allow_inferred=args.allow_inferred_trace,
        )
        rows.append(repaired.to_result_row())
        repairs.append(
            {
                "case_id": case_id,
                "graph_path": str(graph_path),
                "old_trace_id": baseline.trace_id,
                "new_trace_id": repaired.trace_id,
                "repaired": repaired.trace_id != baseline.trace_id,
                "reason": (
                    "graph_trace_span_replacement"
                    if repaired.trace_id != baseline.trace_id
                    else "kept_existing_trace"
                ),
            }
        )

    if missing_graphs and args.fail_on_missing_graph:
        joined = ", ".join(missing_graphs[:8])
        raise FileNotFoundError(f"missing graph contexts for {len(missing_graphs)} cases: {joined}")

    result_payload = realrca_payload_from_rows(
        rows,
        split=args.split,
        model_name=args.model_name,
        agent_description=args.agent_description,
    )
    repaired_case_ids = [item["case_id"] for item in repairs if item["repaired"]]
    audit: dict[str, Any] = {
        "baseline": str(args.baseline),
        "graph_root": str(graph_roots[0]) if graph_roots else "",
        "graph_roots": [str(root) for root in graph_roots],
        "case_count": len(rows),
        "selected_case_count": len(selected_case_ids),
        "repaired_case_count": len(repaired_case_ids),
        "repaired_case_ids": repaired_case_ids,
        "missing_graphs": missing_graphs,
        "repairs": repairs,
    }
    write_json(args.out_result, result_payload)
    write_json(args.out_audit, audit)
    print(
        f"wrote {args.out_result} rows={len(rows)} repaired={len(repaired_case_ids)} "
        f"missing_graphs={len(missing_graphs)}"
    )
    return 0


def enrich_trajectories_command(args: argparse.Namespace) -> int:
    baseline_rows = rows_by_case(args.baseline, source=_source_name(args.baseline))
    ordered_case_ids = _baseline_order(args.baseline)
    audit_payload = load_json(args.audit)
    audit_by_case = {
        item["case_id"]: item
        for item in audit_payload.get("results", [])
        if isinstance(item, dict) and isinstance(item.get("case_id"), str)
    }
    requested_case_ids = (
        _selected_case_ids(args.case_id, ordered_case_ids) if args.case_id else set(audit_by_case)
    )
    graph_roots = _graph_roots(args)
    probed_suffixes = (
        _probed_suffixes(args.leaderboard, args.team_name) if args.skip_probed_cases else set()
    )
    rows: list[dict[str, str]] = []
    decisions: list[dict[str, Any]] = []
    missing_graphs: list[str] = []

    for case_id in ordered_case_ids:
        baseline = baseline_rows[case_id]
        audit_case = audit_by_case.get(case_id)
        if case_id not in requested_case_ids or audit_case is None:
            rows.append(baseline.to_result_row())
            continue
        if _case_suffix(case_id) in probed_suffixes:
            rows.append(baseline.to_result_row())
            decisions.append(
                {
                    "case_id": case_id,
                    "changed": False,
                    "reason": "skipped: case suffix already appears in team leaderboard probes",
                    "candidate": baseline.to_result_row(),
                }
            )
            continue

        graph_path = _find_graph_context_path(graph_roots, args.split, case_id)
        if graph_path is None:
            missing_graphs.append(case_id)
            rows.append(baseline.to_result_row())
            decisions.append(
                {
                    "case_id": case_id,
                    "changed": False,
                    "reason": "missing_graph_context",
                    "candidate": baseline.to_result_row(),
                }
            )
            continue

        bundle = build_evidence_bundle_cached(
            graph_path,
            evidence_limit=args.evidence_limit,
            hypothesis_limit=args.hypothesis_limit,
            support_limit=args.support_limit,
        )
        decision = enrich_answer(
            baseline,
            bundle,
            terms_from_audit_case(audit_case),
            max_terms=args.max_terms,
            max_answer_chars=args.max_answer_chars,
            min_term_score=args.min_term_score,
        )
        rows.append(decision.candidate.to_result_row())
        payload = decision.to_dict()
        payload["graph_path"] = str(graph_path)
        decisions.append(payload)

    if missing_graphs and args.fail_on_missing_graph:
        joined = ", ".join(missing_graphs[:8])
        raise FileNotFoundError(f"missing graph contexts for {len(missing_graphs)} cases: {joined}")

    result_payload = realrca_payload_from_rows(
        rows,
        split=args.split,
        model_name=args.model_name,
        agent_description=args.agent_description,
    )
    changed_case_ids = [item["case_id"] for item in decisions if item.get("changed")]
    audit: dict[str, Any] = {
        "baseline": str(args.baseline),
        "trajectory_audit": str(args.audit),
        "graph_root": str(graph_roots[0]) if graph_roots else "",
        "graph_roots": [str(root) for root in graph_roots],
        "case_count": len(rows),
        "selected_case_count": len(requested_case_ids),
        "skip_probed_cases": bool(args.skip_probed_cases),
        "probed_suffix_count": len(probed_suffixes),
        "changed_case_count": len(changed_case_ids),
        "changed_case_ids": changed_case_ids,
        "missing_graphs": missing_graphs,
        "decisions": decisions,
    }
    write_json(args.out_result, result_payload)
    write_json(args.out_audit, audit)
    print(
        f"wrote {args.out_result} rows={len(rows)} changed={len(changed_case_ids)} "
        f"missing_graphs={len(missing_graphs)}"
    )
    return 0


def _candidate_scores_for_generation(
    *,
    baseline: CandidateAnswer,
    candidates_by_case: dict[str, list[CandidateAnswer]],
    bundle: Any,
    feedback_ledger: ProbeFeedbackLedger | None = None,
) -> list[tuple[CandidateAnswer, Any]]:
    scored = [(baseline, score_candidate(baseline, baseline, bundle))]
    probe_feedback = (
        feedback_ledger.for_case_id(baseline.case_id) if feedback_ledger is not None else None
    )
    for candidate in candidates_by_case.get(baseline.case_id, []):
        if candidate.source == baseline.source:
            continue
        if (
            candidate.trace_id == baseline.trace_id
            and candidate.diagnosis_output == baseline.diagnosis_output
        ):
            continue
        scored.append(
            (candidate, score_candidate(candidate, baseline, bundle, probe_feedback=probe_feedback))
        )
    return scored


def _run_process_json(
    cmd: list[str],
    *,
    stdin: str | None = None,
    timeout: int = 120,
) -> dict[str, Any]:
    env = os.environ.copy()
    env.setdefault("NO_COLOR", "1")
    proc = subprocess.run(
        cmd,
        input=stdin,
        text=True,
        capture_output=True,
        timeout=timeout,
        check=False,
        env=env,
    )
    if proc.returncode != 0:
        message = proc.stderr.strip() or proc.stdout.strip() or f"exit {proc.returncode}"
        raise RuntimeError(message)
    try:
        value = json.loads(proc.stdout)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"invalid json from command: {proc.stdout[:1000]}") from exc
    if not isinstance(value, dict):
        raise RuntimeError(f"expected json object from command, got {type(value).__name__}")
    return value


def _run_dma_json(
    cmd: list[str],
    args: argparse.Namespace,
    *,
    stdin: str | None = None,
) -> dict[str, Any]:
    last_error: Exception | None = None
    for attempt in range(1, args.dma_api_retries + 1):
        try:
            return _run_process_json(
                cmd,
                stdin=stdin,
                timeout=args.dma_command_timeout_sec,
            )
        except (RuntimeError, subprocess.TimeoutExpired) as exc:
            last_error = exc
            text = str(exc).lower()
            retryable = any(
                marker in text for marker in ("502", "503", "504", "timeout", "timed out")
            )
            if attempt == args.dma_api_retries or not retryable:
                raise
            time.sleep(min(30.0, 2.0 * attempt))
    if last_error is not None:
        raise last_error
    raise RuntimeError("dma command was not executed")


def _dma_id(payload: dict[str, Any], keys: tuple[str, ...], *, prefix: str = "") -> str:
    for key in keys:
        value = payload.get(key)
        if isinstance(value, str) and value and (not prefix or value.startswith(prefix)):
            return value
    raise RuntimeError(f"cannot find DMA id in keys {keys}: {payload}")


def _wait_dma_run(run_id: str, args: argparse.Namespace) -> dict[str, Any]:
    deadline = time.time() + args.timeout_sec
    last: dict[str, Any] = {}
    while time.time() < deadline:
        last = _run_dma_json(
            [
                str(args.dma_bin),
                "task",
                "get",
                run_id,
                "-o",
                "json",
                "--no-interactive",
            ],
            args,
        )
        status = str(last.get("status", "")).lower()
        if status in TERMINAL_STATUSES:
            return last
        time.sleep(args.poll_interval_sec)
    last = _run_dma_json(
        [str(args.dma_bin), "task", "get", run_id, "-o", "json", "--no-interactive"],
        args,
    )
    last["runner_timeout"] = True
    return last


def _message_texts(run: dict[str, Any]) -> list[str]:
    texts: list[str] = []
    for item in run.get("output", []):
        if not isinstance(item, dict):
            continue
        data = item.get("data")
        if isinstance(data, dict) and isinstance(data.get("output"), str):
            texts.append(data["output"])
    return texts


def _create_dma_session(case_id: str, args: argparse.Namespace) -> dict[str, Any]:
    cmd = [
        str(args.dma_bin),
        "session",
        "create",
        "--agent-id",
        args.agent_id,
        "--env-id",
        args.env_id,
        "--title",
        f"realrca-evidence-gen-{case_id[:8]}-{uuid.uuid4().hex[:6]}",
        "-o",
        "json",
        "--no-interactive",
    ]
    if args.llm_config_id:
        cmd.extend(["--llm-config-id", args.llm_config_id])
    return _run_dma_json(cmd, args)


def _create_dma_task(
    *,
    session_id: str,
    prompt: str,
    snapshot_ref: str,
    args: argparse.Namespace,
) -> dict[str, Any]:
    cmd = [
        str(args.dma_bin),
        "task",
        "create",
        session_id,
        "--input",
        "-",
        "--env-override",
        "SF_DATA_PLANE=p2",
        "--env-override",
        "SUNFIRE_AUTO_OPEN=0",
        "--no-stream",
        "-o",
        "json",
        "--no-interactive",
    ]
    if snapshot_ref:
        cmd.extend(["--env-override", f"SF_DATA_REF={snapshot_ref}"])
    return _run_dma_json(cmd, args, stdin=prompt)


def _run_generation_case(
    *,
    case_id: str,
    case: dict[str, Any],
    prompt: str,
    case_dir: Path,
    args: argparse.Namespace,
) -> dict[str, Any]:
    result_path = case_dir / "result.json"
    if result_path.exists() and not args.rerun:
        row = load_json(result_path)
        return {
            "case_id": case_id,
            "status": "cached",
            "row": candidate_row_from_result(row),
            "result_path": str(result_path),
            "elapsed_sec": 0.0,
        }

    start = time.time()
    try:
        session = _create_dma_session(case_id, args)
        write_json(case_dir / "session.json", session)
        task = _create_dma_task(
            session_id=_dma_id(session, ("id", "session_id")),
            prompt=prompt,
            snapshot_ref=_snapshot_ref(case),
            args=args,
        )
        write_json(case_dir / "task-create.json", task)
        run = _wait_dma_run(_dma_id(task, ("run_id", "id"), prefix="run-"), args)
        write_json(case_dir / "run-final.json", run)
        if str(run.get("status", "")).lower() != "completed":
            raise RuntimeError(f"dma run status {run.get('status')}")
        if run.get("runner_timeout"):
            raise RuntimeError("runner timeout")
        result = extract_candidate_result("\n".join(_message_texts(run)))
        if result is None:
            raise RuntimeError("no parseable result json")
        validation_error = validate_candidate_result(case_id, result)
        if validation_error:
            raise RuntimeError(validation_error)
        row = candidate_row_from_result(result)
        write_json(result_path, row)
        return {
            "case_id": case_id,
            "status": "ok",
            "row": row,
            "result_path": str(result_path),
            "elapsed_sec": round(time.time() - start, 3),
        }
    except Exception as exc:
        return {
            "case_id": case_id,
            "status": "failed",
            "error": f"{type(exc).__name__}: {exc}",
            "elapsed_sec": round(time.time() - start, 3),
        }


def generate_candidates_command(args: argparse.Namespace) -> int:
    baseline_rows = rows_by_case(args.baseline, source=_source_name(args.baseline))
    candidate_paths = _candidate_paths(args)
    candidates_by_case = _candidate_pool(candidate_paths)
    ordered_case_ids = _baseline_order(args.baseline)
    selected_case_ids = _selected_case_ids(args.case_id, ordered_case_ids)
    graph_roots = _graph_roots(args)
    case_meta = _case_meta_by_id(args.split, args.dataset_dir)
    probe_agents_by_suffix = _probe_agents_by_suffix(args.leaderboard, args.team_name)
    probed_suffixes = set(probe_agents_by_suffix) if args.skip_probed_cases else set()
    feedback_ledger = _feedback_ledger(args.leaderboard, args.team_name)
    validation_memory = (
        load_validation_memory(args.validation_memory)
        if args.validation_exemplar_limit > 0
        else None
    )
    trajectory_signals = _trajectory_signals_by_case(
        [path for path in args.trajectory_audit if path.exists()],
        limit=args.trajectory_term_limit,
    )
    frontier_contexts = _frontier_context_by_case(args.frontier)
    graph_analogue_contexts = _graph_analogue_context_by_case(args.graph_analogue_report)
    rows: list[dict[str, str]] = []
    statuses: list[dict[str, Any]] = []
    run_inputs: list[tuple[str, dict[str, Any], str, Path]] = []

    for case_id in ordered_case_ids:
        if case_id not in selected_case_ids:
            continue
        case_dir = args.out_dir / args.run_label / args.split / case_id
        case_dir.mkdir(parents=True, exist_ok=True)
        baseline = baseline_rows[case_id]
        if _case_suffix(case_id) in probed_suffixes:
            statuses.append(
                {
                    "case_id": case_id,
                    "status": "skipped",
                    "reason": "case suffix already appears in team leaderboard probes",
                    "previous_probe_agents": probe_agents_by_suffix.get(_case_suffix(case_id), []),
                }
            )
            continue
        graph_path = _find_graph_context_path(graph_roots, args.split, case_id)
        if graph_path is None:
            statuses.append(
                {"case_id": case_id, "status": "failed", "error": "missing_graph_context"}
            )
            continue
        case = case_meta.get(case_id, {"case_id": case_id, "split": args.split})
        graph_context = load_json(graph_path)
        bundle = build_evidence_bundle(
            graph_context,
            evidence_limit=args.evidence_limit,
            hypothesis_limit=args.hypothesis_limit,
            support_limit=args.support_limit,
        )
        package = build_generation_package(
            case=case,
            baseline=baseline,
            bundle=bundle,
            candidate_scores=_candidate_scores_for_generation(
                baseline=baseline,
                candidates_by_case=candidates_by_case,
                bundle=bundle,
                feedback_ledger=feedback_ledger,
            ),
            graph_context=graph_context,
            previous_probe_agents=probe_agents_by_suffix.get(_case_suffix(case_id), []),
            strategy_hint=args.strategy_hint,
            validation_exemplars=[
                item.to_dict()
                for item in match_validation_exemplars(
                    bundle,
                    validation_memory,
                    answer=baseline,
                    limit=args.validation_exemplar_limit,
                )
            ],
            visible_tool_signals=trajectory_signals.get(case_id, []),
            frontier_context=frontier_contexts.get(case_id)
            or frontier_contexts.get(_case_suffix(case_id)),
            graph_analogues=graph_analogue_contexts.get(case_id, []),
            candidate_limit=args.candidate_limit,
            answer_chars=args.answer_chars,
        )
        prompt = render_generation_prompt(package)
        write_json(case_dir / "package.json", package.to_dict())
        (case_dir / "prompt.txt").write_text(prompt, encoding="utf-8")
        statuses.append(
            {
                "case_id": case_id,
                "status": "packaged" if args.dry_run else "queued",
                "graph_path": str(graph_path),
                "prompt_path": str(case_dir / "prompt.txt"),
                "package_path": str(case_dir / "package.json"),
            }
        )
        if not args.dry_run:
            run_inputs.append((case_id, case, prompt, case_dir))

    if run_inputs:
        with concurrent.futures.ThreadPoolExecutor(max_workers=args.concurrency) as executor:
            futures = [
                executor.submit(
                    _run_generation_case,
                    case_id=case_id,
                    case=case,
                    prompt=prompt,
                    case_dir=case_dir,
                    args=args,
                )
                for case_id, case, prompt, case_dir in run_inputs
            ]
            for future in concurrent.futures.as_completed(futures):
                status = future.result()
                statuses.append(status)
                if isinstance(status.get("row"), dict):
                    rows.append(status["row"])
                print(
                    f"{status['case_id']} {status['status']} "
                    f"{status.get('elapsed_sec', 0):.1f}s {status.get('error') or ''}",
                    flush=True,
                )

    result_payload = realrca_payload_from_rows(
        rows,
        split=args.split,
        model_name=args.model_name,
        agent_description=args.agent_description,
    )
    write_json(args.out_result, result_payload)
    write_json(
        args.out_audit,
        {
            "baseline": str(args.baseline),
            "graph_roots": [str(root) for root in graph_roots],
            "candidate_files": [str(path) for path in candidate_paths],
            "case_count": len(selected_case_ids),
            "generated_case_count": len(rows),
            "dry_run": bool(args.dry_run),
            "skip_probed_cases": bool(args.skip_probed_cases),
            "strategy_hint": args.strategy_hint,
            "frontier": str(args.frontier) if args.frontier else "",
            "validation_memory": str(args.validation_memory),
            "validation_exemplar_limit": args.validation_exemplar_limit,
            "trajectory_audits": [str(path) for path in args.trajectory_audit if path.exists()],
            "trajectory_term_limit": args.trajectory_term_limit,
            "statuses": statuses,
        },
    )
    print(f"wrote {args.out_result} generated={len(rows)} dry_run={args.dry_run}")
    failures = [item for item in statuses if item.get("status") == "failed"]
    return 1 if failures and args.fail_on_generation_error else 0


def _safe_source_name(value: str) -> str:
    safe = "".join(char if char.isalnum() or char in {"-", "_"} else "-" for char in value)
    return safe[:80] or "candidate"


def _run_pairwise_verifier_case(
    *,
    case_id: str,
    case: dict[str, Any],
    candidate: CandidateAnswer,
    prompt: str,
    pair_dir: Path,
    baseline_score: Any,
    candidate_score: Any,
    args: argparse.Namespace,
) -> dict[str, Any]:
    result_path = pair_dir / "verdict.json"
    if result_path.exists() and not args.rerun:
        parsed = parse_pairwise_verifier_decision(case_id, load_json(result_path))
        accepted, accept_reason = should_accept_pairwise_decision(
            decision=parsed,
            baseline_score=baseline_score,
            candidate_score=candidate_score,
            min_confidence=args.min_confidence,
            min_support_margin=args.min_support_margin,
        )
        return {
            "case_id": case_id,
            "candidate_source": candidate.source,
            "status": "cached",
            "decision": parsed.to_dict(),
            "accepted": accepted,
            "accept_reason": accept_reason,
            "candidate": candidate.to_result_row(),
            "baseline_score": baseline_score.to_dict(),
            "candidate_score": candidate_score.to_dict(),
            "result_path": str(result_path),
            "elapsed_sec": 0.0,
        }

    start = time.time()
    try:
        session = _create_dma_session(case_id, args)
        write_json(pair_dir / "session.json", session)
        task = _create_dma_task(
            session_id=_dma_id(session, ("id", "session_id")),
            prompt=prompt,
            snapshot_ref=_snapshot_ref(case),
            args=args,
        )
        write_json(pair_dir / "task-create.json", task)
        run = _wait_dma_run(_dma_id(task, ("run_id", "id"), prefix="run-"), args)
        write_json(pair_dir / "run-final.json", run)
        if str(run.get("status", "")).lower() != "completed":
            raise RuntimeError(f"dma run status {run.get('status')}")
        if run.get("runner_timeout"):
            raise RuntimeError("runner timeout")
        result = extract_pairwise_verifier_result("\n".join(_message_texts(run)))
        if result is None:
            raise RuntimeError("no parseable verifier json")
        parsed = parse_pairwise_verifier_decision(case_id, result)
        write_json(result_path, parsed.to_dict())
        accepted, accept_reason = should_accept_pairwise_decision(
            decision=parsed,
            baseline_score=baseline_score,
            candidate_score=candidate_score,
            min_confidence=args.min_confidence,
            min_support_margin=args.min_support_margin,
        )
        return {
            "case_id": case_id,
            "candidate_source": candidate.source,
            "status": "ok",
            "decision": parsed.to_dict(),
            "accepted": accepted,
            "accept_reason": accept_reason,
            "candidate": candidate.to_result_row(),
            "baseline_score": baseline_score.to_dict(),
            "candidate_score": candidate_score.to_dict(),
            "result_path": str(result_path),
            "elapsed_sec": round(time.time() - start, 3),
        }
    except Exception as exc:
        return {
            "case_id": case_id,
            "candidate_source": candidate.source,
            "status": "failed",
            "error": f"{type(exc).__name__}: {exc}",
            "elapsed_sec": round(time.time() - start, 3),
        }


def verify_candidates_command(args: argparse.Namespace) -> int:
    baseline_rows = rows_by_case(args.baseline, source=_source_name(args.baseline))
    candidate_paths = _candidate_paths(args)
    candidates_by_case = _candidate_pool(candidate_paths)
    ordered_case_ids = _baseline_order(args.baseline)
    selected_case_ids = _selected_case_ids(args.case_id, ordered_case_ids)
    graph_roots = _graph_roots(args)
    case_meta = _case_meta_by_id(args.split, args.dataset_dir)
    probe_agents_by_suffix = _probe_agents_by_suffix(args.leaderboard, args.team_name)
    feedback_ledger = _feedback_ledger(args.leaderboard, args.team_name)
    statuses: list[dict[str, Any]] = []
    run_inputs: list[tuple[str, dict[str, Any], CandidateAnswer, str, Path, Any, Any]] = []

    for case_id in ordered_case_ids:
        if case_id not in selected_case_ids:
            continue
        baseline = baseline_rows[case_id]
        graph_path = _find_graph_context_path(graph_roots, args.split, case_id)
        if graph_path is None:
            statuses.append(
                {"case_id": case_id, "status": "failed", "error": "missing_graph_context"}
            )
            continue
        graph_context = load_json(graph_path)
        bundle = build_evidence_bundle(
            graph_context,
            evidence_limit=args.evidence_limit,
            hypothesis_limit=args.hypothesis_limit,
            support_limit=args.support_limit,
        )
        baseline_score = score_candidate(baseline, baseline, bundle)
        scored_candidates = []
        for candidate in candidates_by_case.get(case_id, []):
            if candidate.source == baseline.source:
                continue
            if (
                candidate.trace_id == baseline.trace_id
                and candidate.diagnosis_output == baseline.diagnosis_output
            ):
                continue
            candidate_score = score_candidate(
                candidate,
                baseline,
                bundle,
                probe_feedback=feedback_ledger.for_case_id(case_id)
                if feedback_ledger is not None
                else None,
            )
            scored_candidates.append((candidate, candidate_score))
        scored_candidates.sort(
            key=lambda item: (
                len(item[1].risk_flags),
                -item[1].graph_support,
                -item[1].baseline_retention,
                item[1].novelty,
                item[0].source,
            )
        )
        if not scored_candidates:
            statuses.append(
                {"case_id": case_id, "status": "skipped", "reason": "no candidate rows"}
            )
            continue
        case = case_meta.get(case_id, {"case_id": case_id, "split": args.split})
        for candidate, candidate_score in scored_candidates[: args.candidate_limit]:
            pair_dir = (
                args.out_dir
                / args.run_label
                / args.split
                / case_id
                / _safe_source_name(candidate.source)
            )
            pair_dir.mkdir(parents=True, exist_ok=True)
            package = build_pairwise_verifier_package(
                case=case,
                baseline=baseline,
                candidate=candidate,
                bundle=bundle,
                baseline_score=baseline_score,
                candidate_score=candidate_score,
                previous_probe_agents=probe_agents_by_suffix.get(_case_suffix(case_id), []),
                strategy_hint=args.strategy_hint,
            )
            prompt = render_pairwise_verifier_prompt(package)
            write_json(pair_dir / "package.json", package.to_dict())
            (pair_dir / "prompt.txt").write_text(prompt, encoding="utf-8")
            statuses.append(
                {
                    "case_id": case_id,
                    "candidate_source": candidate.source,
                    "status": "packaged" if args.dry_run else "queued",
                    "prompt_path": str(pair_dir / "prompt.txt"),
                    "package_path": str(pair_dir / "package.json"),
                }
            )
            if not args.dry_run:
                run_inputs.append(
                    (
                        case_id,
                        case,
                        candidate,
                        prompt,
                        pair_dir,
                        baseline_score,
                        candidate_score,
                    )
                )

    completed: list[dict[str, Any]] = []
    if run_inputs:
        with concurrent.futures.ThreadPoolExecutor(max_workers=args.concurrency) as executor:
            futures = [
                executor.submit(
                    _run_pairwise_verifier_case,
                    case_id=case_id,
                    case=case,
                    candidate=candidate,
                    prompt=prompt,
                    pair_dir=pair_dir,
                    baseline_score=baseline_score,
                    candidate_score=candidate_score,
                    args=args,
                )
                for case_id, case, candidate, prompt, pair_dir, baseline_score, candidate_score in run_inputs
            ]
            for future in concurrent.futures.as_completed(futures):
                status = future.result()
                completed.append(status)
                statuses.append(status)
                verdict = (status.get("decision") or {}).get("verdict", "-")
                print(
                    f"{status['case_id']} {status.get('candidate_source')} {status['status']} "
                    f"{verdict} accepted={status.get('accepted', False)} "
                    f"{status.get('elapsed_sec', 0):.1f}s {status.get('error') or status.get('accept_reason') or ''}",
                    flush=True,
                )

    accepted_by_case: dict[str, dict[str, Any]] = {}
    for status in completed:
        if not status.get("accepted"):
            continue
        case_id = str(status.get("case_id") or "")
        decision = status.get("decision") if isinstance(status.get("decision"), dict) else {}
        current = accepted_by_case.get(case_id)
        if current is not None:
            current_decision = (
                current.get("decision") if isinstance(current.get("decision"), dict) else {}
            )
            if float(current_decision.get("confidence") or 0.0) >= float(
                decision.get("confidence") or 0.0
            ):
                continue
        accepted_by_case[case_id] = status

    rows: list[dict[str, str]] = []
    for case_id in ordered_case_ids:
        accepted = accepted_by_case.get(case_id)
        if accepted and isinstance(accepted.get("candidate"), dict):
            rows.append(accepted["candidate"])
        else:
            rows.append(baseline_rows[case_id].to_result_row())

    write_json(
        args.out_audit,
        {
            "baseline": str(args.baseline),
            "graph_roots": [str(root) for root in graph_roots],
            "candidate_files": [str(path) for path in candidate_paths],
            "case_count": len(selected_case_ids),
            "verified_pair_count": len(run_inputs),
            "accepted_replacements": [
                {
                    "case_id": case_id,
                    "candidate_source": status.get("candidate_source"),
                    "confidence": (status.get("decision") or {}).get("confidence"),
                    "reason": (status.get("decision") or {}).get("reason"),
                }
                for case_id, status in sorted(accepted_by_case.items())
            ],
            "dry_run": bool(args.dry_run),
            "strategy_hint": args.strategy_hint,
            "statuses": statuses,
        },
    )
    write_json(
        args.out_result,
        realrca_payload_from_rows(
            rows,
            split=args.split,
            model_name=args.model_name,
            agent_description=args.agent_description,
        ),
    )
    print(
        f"wrote {args.out_result} pairs={len(run_inputs)} "
        f"accepted={len(accepted_by_case)} dry_run={args.dry_run}"
    )
    failures = [item for item in statuses if item.get("status") == "failed"]
    return 1 if failures and args.fail_on_verifier_error else 0


def score_validation_command(args: argparse.Namespace) -> int:
    summary = score_validation_file(args.result, dataset_dir=args.dataset_dir)
    if args.out:
        write_validation_summary(summary, args.out)
    print(
        f"cases={summary.case_count} avg_loose={summary.avg_loose_score} "
        f"avg_critical={summary.avg_critical_coverage}"
    )
    for case in summary.cases[: args.show_cases]:
        print(
            f"{case.case_id} {case.case_type} loose={case.loose_score} "
            f"critical={case.critical_coverage} missing={case.missing_critical_items[:3]}"
        )
    return 0


def synthesize_command(args: argparse.Namespace) -> int:
    rows: list[dict[str, str]] = []
    graph_roots = _graph_roots(args)
    ordered_case_ids = [
        str(case["case_id"])
        for case in load_cases(args.split, args.dataset_dir)
        if isinstance(case, dict) and isinstance(case.get("case_id"), str)
    ]
    selected = _selected_case_ids(args.case_id, ordered_case_ids)
    missing_graphs: list[str] = []
    for case_id in ordered_case_ids:
        if selected and case_id not in selected:
            continue
        graph_path = _find_graph_context_path(graph_roots, args.split, case_id)
        if graph_path is None:
            missing_graphs.append(case_id)
            continue
        bundle = build_evidence_bundle_cached(
            graph_path,
            evidence_limit=args.evidence_limit,
            hypothesis_limit=args.hypothesis_limit,
            support_limit=args.support_limit,
        )
        rows.append(synthesize_answer(bundle, source=args.source_name).to_result_row())
    payload = realrca_payload_from_rows(
        rows,
        split=args.split,
        model_name=args.model_name,
        agent_description=args.agent_description,
    )
    write_json(args.out_result, payload)
    print(f"wrote {args.out_result} rows={len(rows)} missing_graphs={len(missing_graphs)}")
    return 0


def triage_command(args: argparse.Namespace) -> int:
    graph_roots = _graph_roots(args)
    candidate_paths = _candidate_paths(args)
    report = build_triage_report(
        baseline_path=args.baseline,
        graph_roots=graph_roots,
        split=args.split,
        candidate_paths=candidate_paths,
        dataset_dir=args.dataset_dir,
        case_ids=args.case_id,
        leaderboard_path=args.leaderboard,
        team_name=args.team_name,
    )
    write_json(args.out_json, report.to_dict())
    if args.out_md:
        args.out_md.parent.mkdir(parents=True, exist_ok=True)
        args.out_md.write_text(
            render_triage_markdown(report, limit=args.markdown_limit),
            encoding="utf-8",
        )
    print(
        f"wrote {args.out_json} cases={report.case_count} "
        f"top={report.cases[0].case_id if report.cases else '-'}"
    )
    return 0


def coverage_gaps_command(args: argparse.Namespace) -> int:
    graph_roots = _graph_roots(args)
    candidate_paths = _candidate_paths(args)
    report = build_coverage_gap_report(
        baseline_path=args.baseline,
        graph_roots=graph_roots,
        split=args.split,
        candidate_paths=candidate_paths,
        dataset_dir=args.dataset_dir,
        case_ids=args.case_id,
        leaderboard_path=args.leaderboard,
        team_name=args.team_name,
    )
    write_json(args.out_json, report.to_dict())
    if args.out_md:
        args.out_md.parent.mkdir(parents=True, exist_ok=True)
        args.out_md.write_text(
            render_coverage_gap_markdown(report, limit=args.markdown_limit),
            encoding="utf-8",
        )
    print(
        f"wrote {args.out_json} cases={report.case_count} "
        f"top={report.cases[0].case_id if report.cases else '-'}"
    )
    return 0


def case_analogues_command(args: argparse.Namespace) -> int:
    graph_roots = _graph_roots(args)
    report = build_case_analogue_report(
        baseline_path=args.baseline,
        graph_roots=graph_roots,
        split=args.split,
        validation_memory_path=args.validation_memory,
        dataset_dir=args.dataset_dir,
        case_ids=args.case_id,
        match_limit=args.match_limit,
        leaderboard_path=args.leaderboard,
        team_name=args.team_name,
    )
    write_json(args.out_json, report.to_dict())
    if args.out_md:
        args.out_md.parent.mkdir(parents=True, exist_ok=True)
        args.out_md.write_text(
            render_case_analogue_markdown(report, limit=args.markdown_limit),
            encoding="utf-8",
        )
    print(
        f"wrote {args.out_json} cases={report.case_count} "
        f"top={report.cases[0].case_id if report.cases else '-'}"
    )
    return 0


def raw_inventory_command(args: argparse.Namespace) -> int:
    graph_roots = _graph_roots(args)
    report = build_raw_inventory_report(
        baseline_path=args.baseline,
        graph_roots=graph_roots,
        split=args.split,
        dataset_dir=args.dataset_dir,
        case_ids=args.case_id,
        leaderboard_path=args.leaderboard,
        team_name=args.team_name,
        top_files_per_case=args.top_files_per_case,
    )
    write_json(args.out_json, report.to_dict())
    if args.out_md:
        args.out_md.parent.mkdir(parents=True, exist_ok=True)
        args.out_md.write_text(
            render_raw_inventory_markdown(report, limit=args.markdown_limit),
            encoding="utf-8",
        )
    print(
        f"wrote {args.out_json} cases={report.case_count} "
        f"top={report.cases[0].case_id if report.cases else '-'}"
    )
    return 0


def frontier_command(args: argparse.Namespace) -> int:
    graph_roots = _graph_roots(args)
    report = build_frontier_report(
        baseline_path=args.baseline,
        graph_roots=graph_roots,
        split=args.split,
        dataset_dir=args.dataset_dir,
        validation_memory_path=args.validation_memory,
        case_ids=args.case_id,
        leaderboard_path=args.leaderboard,
        team_name=args.team_name,
        tomography_path=args.tomography,
        results_dir=args.results_dir,
        reference_agent_name=args.reference_agent_name or None,
        top_files_per_case=args.top_files_per_case,
        match_limit=args.match_limit,
    )
    write_json(args.out_json, report.to_dict())
    if args.out_md:
        args.out_md.parent.mkdir(parents=True, exist_ok=True)
        args.out_md.write_text(
            render_frontier_markdown(report, limit=args.markdown_limit),
            encoding="utf-8",
        )
    print(
        f"wrote {args.out_json} cases={report.case_count} "
        f"top={report.cases[0].case_id if report.cases else '-'}"
    )
    return 0


def boundary_deltas_command(args: argparse.Namespace) -> int:
    graph_roots = _graph_roots(args)
    report = build_boundary_delta_report(
        baseline_path=args.baseline,
        graph_roots=graph_roots,
        split=args.split,
        dataset_dir=args.dataset_dir,
        case_ids=args.case_id,
        leaderboard_path=args.leaderboard,
        team_name=args.team_name,
    )
    write_json(args.out_json, report.to_dict())
    if args.out_md:
        args.out_md.parent.mkdir(parents=True, exist_ok=True)
        args.out_md.write_text(
            render_boundary_delta_markdown(report, limit=args.markdown_limit),
            encoding="utf-8",
        )
    print(
        f"wrote {args.out_json} cases={report.case_count} "
        f"top={report.cases[0].case_id if report.cases else '-'}"
    )
    return 0


def calibrate_command(args: argparse.Namespace) -> int:
    graph_roots = _graph_roots(args)
    report = build_calibration_report(
        graph_roots=graph_roots,
        split=args.split,
        dataset_dir=args.dataset_dir,
        hypothesis_limit=args.hypothesis_limit,
        min_overlap=args.min_overlap,
        min_recall=args.min_recall,
    )
    write_json(args.out_json, report.to_dict())
    if args.out_md:
        args.out_md.parent.mkdir(parents=True, exist_ok=True)
        args.out_md.write_text(
            render_calibration_markdown(report, limit=args.markdown_limit),
            encoding="utf-8",
        )
    print(
        f"wrote {args.out_json} cases={report.case_count} "
        f"top1={report.top1_hit_rate} top3={report.top3_hit_rate} "
        f"mrr={report.mean_reciprocal_rank}"
    )
    return 0


def selector_calibration_command(args: argparse.Namespace) -> int:
    graph_roots = _graph_roots(args)
    candidate_paths = _candidate_paths(args)
    report = build_selector_calibration_report(
        result_paths=candidate_paths,
        graph_roots=graph_roots,
        split=args.split,
        dataset_dir=args.dataset_dir,
        baseline_path=args.baseline,
        evidence_limit=args.evidence_limit,
        hypothesis_limit=args.hypothesis_limit,
        support_limit=args.support_limit,
    )
    write_json(args.out_json, report.to_dict())
    if args.out_md:
        args.out_md.parent.mkdir(parents=True, exist_ok=True)
        args.out_md.write_text(
            render_selector_calibration_markdown(report, limit=args.markdown_limit),
            encoding="utf-8",
        )
    print(
        f"wrote {args.out_json} cases={report.case_count} "
        f"candidates={report.candidate_count} categories={report.category_counts}"
    )
    return 0


def contract_gaps_command(args: argparse.Namespace) -> int:
    report = build_contract_gap_report(
        analogue_path=args.analogue,
        baseline_path=args.baseline,
        score_boundary_path=args.score_boundary,
        dataset_dir=args.dataset_dir,
        match_limit=args.match_limit,
        min_similarity=args.min_similarity,
        item_coverage_threshold=args.item_coverage_threshold,
    )
    write_json(args.out_json, report.to_dict())
    if args.out_md:
        args.out_md.parent.mkdir(parents=True, exist_ok=True)
        args.out_md.write_text(
            render_contract_gap_markdown(report, limit=args.markdown_limit),
            encoding="utf-8",
        )
    print(
        f"contract gaps cases={report.case_count} items={report.item_count} "
        f"actions={dict(report.action_counts)} wrote {args.out_json}"
    )
    return 0


def tomography_command(args: argparse.Namespace) -> int:
    report = build_tomography_report(
        leaderboard_path=args.leaderboard,
        reference_result_path=args.reference,
        results_dir=args.results_dir or REALRCA_DMA,
        team_name=args.team_name,
        reference_agent_name=args.reference_agent_name,
    )
    write_json(args.out_json, report.to_dict())
    if args.out_md:
        args.out_md.parent.mkdir(parents=True, exist_ok=True)
        args.out_md.write_text(
            render_tomography_markdown(report, limit=args.markdown_limit),
            encoding="utf-8",
        )
    print(
        f"wrote {args.out_json} matched={report.matched_submission_count} "
        f"inferred={report.inferred_answer_count} positive={report.positive_answer_count}"
    )
    return 0


def augment_graph_command(args: argparse.Namespace) -> int:
    graph_context = load_json(args.graph)
    run_payload = load_json(args.run_final)
    augmented = augment_graph_context_with_trajectory(
        graph_context,
        run_payload,
        source=args.source,
    )
    write_json(args.out, augmented)
    evidence_count = len(augmented.get("evidence") or [])
    candidate_count = len(augmented.get("root_candidates") or [])
    print(f"wrote {args.out} evidence={evidence_count} candidates={candidate_count}")
    return 0


def augment_graphs_command(args: argparse.Namespace) -> int:
    case_ids = args.case_id or [
        path.parent.name
        for path in sorted((args.graph_root / args.split).glob("*/graph_context.json"))
    ]
    statuses: list[dict[str, Any]] = []
    for case_id in case_ids:
        graph_path = graph_context_path(args.graph_root, args.split, case_id)
        run_path = args.run_root / args.split / case_id / "run-final.json"
        out_path = graph_context_path(args.out_root, args.split, case_id)
        if not graph_path.exists():
            statuses.append({"case_id": case_id, "status": "skipped", "reason": "missing graph"})
            continue
        if not run_path.exists():
            statuses.append(
                {"case_id": case_id, "status": "skipped", "reason": "missing run-final"}
            )
            continue
        graph_context = load_json(graph_path)
        before_evidence = len(graph_context.get("evidence") or [])
        before_candidates = len(graph_context.get("root_candidates") or [])
        run_payload = load_json(run_path)
        augmented = augment_graph_context_with_trajectory(
            graph_context,
            run_payload,
            source=args.source,
        )
        write_json(out_path, augmented)
        after_evidence = len(augmented.get("evidence") or [])
        after_candidates = len(augmented.get("root_candidates") or [])
        statuses.append(
            {
                "case_id": case_id,
                "status": "wrote",
                "out": str(out_path),
                "evidence_added": after_evidence - before_evidence,
                "candidates_added": after_candidates - before_candidates,
            }
        )
    payload = {
        "split": args.split,
        "graph_root": str(args.graph_root),
        "run_root": str(args.run_root),
        "out_root": str(args.out_root),
        "source": args.source,
        "statuses": statuses,
    }
    if args.out_json:
        write_json(args.out_json, payload)
    wrote = sum(1 for item in statuses if item["status"] == "wrote")
    skipped = len(statuses) - wrote
    print(f"augmented graphs wrote={wrote} skipped={skipped}")
    return 0


def augment_resolved_graphs_command(args: argparse.Namespace) -> int:
    graph_roots = _graph_roots(args)
    ordered_case_ids = [
        str(item["case_id"])
        for item in load_cases(args.split, args.dataset_dir)
        if isinstance(item, dict) and isinstance(item.get("case_id"), str)
    ]
    selected = _selected_case_ids(args.case_id, ordered_case_ids)
    case_ids = [case_id for case_id in ordered_case_ids if case_id in selected]
    statuses: list[dict[str, Any]] = []
    for case_id in case_ids:
        graph_path = _find_graph_context_path(graph_roots, args.split, case_id)
        run_path = args.run_root / args.split / case_id / "run-final.json"
        out_path = graph_context_path(args.out_root, args.split, case_id)
        if graph_path is None:
            statuses.append({"case_id": case_id, "status": "skipped", "reason": "missing graph"})
            continue
        if not run_path.exists():
            statuses.append(
                {"case_id": case_id, "status": "skipped", "reason": "missing run-final"}
            )
            continue
        graph_context = load_json(graph_path)
        before_evidence = len(graph_context.get("evidence") or [])
        before_candidates = len(graph_context.get("root_candidates") or [])
        run_payload = load_json(run_path)
        augmented = augment_graph_context_with_trajectory(
            graph_context,
            run_payload,
            source=args.source,
        )
        after_evidence = len(augmented.get("evidence") or [])
        after_candidates = len(augmented.get("root_candidates") or [])
        evidence_added = after_evidence - before_evidence
        candidates_added = after_candidates - before_candidates
        if args.changed_only and not evidence_added and not candidates_added:
            statuses.append(
                {
                    "case_id": case_id,
                    "status": "unchanged",
                    "source_graph": str(graph_path),
                    "evidence_added": 0,
                    "candidates_added": 0,
                }
            )
            continue
        write_json(out_path, augmented)
        statuses.append(
            {
                "case_id": case_id,
                "status": "wrote",
                "source_graph": str(graph_path),
                "out": str(out_path),
                "evidence_added": evidence_added,
                "candidates_added": candidates_added,
            }
        )
    payload = {
        "split": args.split,
        "graph_roots": [str(root) for root in graph_roots],
        "run_root": str(args.run_root),
        "out_root": str(args.out_root),
        "source": args.source,
        "changed_only": args.changed_only,
        "hidden_test_reference_used": False,
        "statuses": statuses,
    }
    if args.out_json:
        write_json(args.out_json, payload)
    wrote = sum(1 for item in statuses if item["status"] == "wrote")
    skipped = sum(1 for item in statuses if item["status"] == "skipped")
    unchanged = sum(1 for item in statuses if item["status"] == "unchanged")
    added = sum(int(item.get("candidates_added") or 0) for item in statuses)
    print(
        f"augmented resolved graphs wrote={wrote} candidates_added={added} "
        f"skipped={skipped} unchanged={unchanged}"
    )
    return 0


def index_graphs_command(args: argparse.Namespace) -> int:
    if args.resolved_label:
        stats = [
            index_resolved_graphs(
                _graph_roots(args),
                graph_label=args.resolved_label,
                db_path=args.db,
                split=args.split,
            )
        ]
    else:
        stats = index_graph_roots(
            _graph_roots(args),
            db_path=args.db,
            split=args.split,
        )
    write_json(args.out_json, {"db": str(args.db), "stats": [item.to_dict() for item in stats]})
    for item in stats:
        print(
            f"{item.graph_label} {item.split} cases={item.case_count} "
            f"nodes={item.node_count} edges={item.edge_count} "
            f"evidence={item.evidence_count} roots={item.root_candidate_count}"
        )
    print(f"wrote {args.out_json}")
    return 0


def graph_analogues_command(args: argparse.Namespace) -> int:
    report = build_graph_analogue_report(
        db_path=args.db,
        split=args.split,
        query_graph_label=args.query_label,
        search_graph_labels=args.search_label,
        search_splits=args.search_split,
        case_ids=args.case_id,
        match_limit=args.match_limit,
        leaderboard_path=args.leaderboard,
        team_name=args.team_name,
    )
    write_json(args.out_json, report.to_dict())
    if args.out_md:
        args.out_md.parent.mkdir(parents=True, exist_ok=True)
        args.out_md.write_text(
            render_graph_analogue_markdown(report, limit=args.markdown_limit),
            encoding="utf-8",
        )
    print(
        f"graph analogues cases={report.case_count} "
        f"categories={dict(report.category_counts)} wrote {args.out_json}"
    )
    return 0


def answer_outliers_command(args: argparse.Namespace) -> int:
    report = build_answer_outlier_report(
        baseline_path=args.baseline,
        internal_analogue_path=args.internal_analogue,
        public_analogue_path=args.public_analogue,
        frontier_path=args.frontier,
        case_ids=args.case_id,
    )
    write_json(args.out_json, report.to_dict())
    if args.out_md:
        args.out_md.parent.mkdir(parents=True, exist_ok=True)
        args.out_md.write_text(
            render_answer_outlier_markdown(report, limit=args.markdown_limit),
            encoding="utf-8",
        )
    print(
        f"answer outliers cases={report.case_count} "
        f"categories={dict(report.category_counts)} wrote {args.out_json}"
    )
    return 0


def score_boundaries_command(args: argparse.Namespace) -> int:
    report = build_score_boundary_report(
        baseline_path=args.baseline,
        frontier_path=args.frontier,
        tomography_path=args.tomography,
        answer_outlier_path=args.answer_outlier,
        case_ids=args.case_id,
    )
    write_json(args.out_json, report.to_dict())
    if args.out_md:
        args.out_md.parent.mkdir(parents=True, exist_ok=True)
        args.out_md.write_text(
            render_score_boundary_markdown(report, limit=args.markdown_limit),
            encoding="utf-8",
        )
    print(
        f"score boundaries cases={report.case_count} "
        f"actions={dict(report.action_counts)} wrote {args.out_json}"
    )
    return 0


def path_frontier_command(args: argparse.Namespace) -> int:
    graph_roots = _graph_roots(args)
    report = build_path_frontier_report(
        baseline_path=args.baseline,
        graph_roots=graph_roots,
        split=args.split,
        case_ids=args.case_id,
        evidence_limit=args.evidence_limit,
        hypothesis_limit=args.hypothesis_limit,
        support_limit=args.support_limit,
        max_depth=args.max_depth,
        seed_limit=args.seed_limit,
    )
    write_json(args.out_json, report.to_dict())
    if args.out_md:
        args.out_md.parent.mkdir(parents=True, exist_ok=True)
        args.out_md.write_text(
            render_path_frontier_markdown(report, limit=args.markdown_limit),
            encoding="utf-8",
        )
    print(
        f"path frontier cases={report.case_count} "
        f"categories={dict(report.category_counts)} wrote {args.out_json}"
    )
    return 0


def pipeline_status_command(args: argparse.Namespace) -> int:
    status = build_pipeline_status(
        leaderboard_path=args.leaderboard,
        team_name=args.team_name,
        baseline_path=args.baseline,
        selector_audit_path=args.selector_audit,
        score_boundary_path=args.score_boundary,
        tomography_path=args.tomography,
        target_accuracy=args.target_accuracy,
    )
    write_json(args.out_json, status.to_dict())
    if args.out_md:
        args.out_md.parent.mkdir(parents=True, exist_ok=True)
        args.out_md.write_text(render_pipeline_status_markdown(status), encoding="utf-8")
    print(
        f"pipeline status current={status.current_best.accuracy} "
        f"target={status.target_accuracy} ready_to_submit={status.ready_to_submit} "
        f"next={status.next_action} wrote {args.out_json}"
    )
    return 0


def search_graph_command(args: argparse.Namespace) -> int:
    matches = search_nodes(
        args.text,
        db_path=args.db,
        split=args.split,
        case_id=args.case_id,
        graph_labels=args.graph_label,
        kinds=args.kind,
        limit=args.limit,
    )
    payload = {"db": str(args.db), "matches": [item.to_dict() for item in matches]}
    if args.out_json:
        write_json(args.out_json, payload)
    for item in matches:
        print(
            f"{item.graph_label} {item.case_id} {item.kind} "
            f"{item.node_id} overlap={item.overlap} label={item.label}"
        )
    return 0


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="RealRCA graph/ontology evidence bundle tools.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    bundle_parser = subparsers.add_parser("bundle")
    bundle_parser.add_argument("--graph", type=Path, required=True)
    bundle_parser.add_argument("--out", type=Path)
    bundle_parser.add_argument("--evidence-limit", type=int, default=32)
    bundle_parser.add_argument("--hypothesis-limit", type=int, default=10)
    bundle_parser.add_argument("--support-limit", type=int, default=4)
    bundle_parser.set_defaults(func=build_bundle_command)

    causal_paths_parser = subparsers.add_parser("causal-paths")
    causal_paths_parser.add_argument("--graph", type=Path, required=True)
    causal_paths_parser.add_argument("--out-json", type=Path, required=True)
    causal_paths_parser.add_argument("--out-md", type=Path)
    causal_paths_parser.add_argument("--evidence-limit", type=int, default=32)
    causal_paths_parser.add_argument("--hypothesis-limit", type=int, default=10)
    causal_paths_parser.add_argument("--support-limit", type=int, default=4)
    causal_paths_parser.add_argument("--max-depth", type=int, default=5)
    causal_paths_parser.add_argument("--seed-limit", type=int, default=8)
    causal_paths_parser.add_argument("--markdown-limit", type=int, default=20)
    causal_paths_parser.set_defaults(func=causal_paths_command)

    augment_parser = subparsers.add_parser("augment-graph")
    augment_parser.add_argument("--graph", type=Path, required=True)
    augment_parser.add_argument("--run-final", type=Path, required=True)
    augment_parser.add_argument("--out", type=Path, required=True)
    augment_parser.add_argument("--source", default="trajectory")
    augment_parser.set_defaults(func=augment_graph_command)

    augment_graphs_parser = subparsers.add_parser("augment-graphs")
    augment_graphs_parser.add_argument("--graph-root", type=Path, required=True)
    augment_graphs_parser.add_argument("--run-root", type=Path, required=True)
    augment_graphs_parser.add_argument("--out-root", type=Path, required=True)
    augment_graphs_parser.add_argument("--split", default="test")
    augment_graphs_parser.add_argument("--case-id", action="append", default=[])
    augment_graphs_parser.add_argument("--source", default="trajectory")
    augment_graphs_parser.add_argument("--out-json", type=Path)
    augment_graphs_parser.set_defaults(func=augment_graphs_command)

    augment_resolved_parser = subparsers.add_parser("augment-resolved-graphs")
    _add_graph_root_args(augment_resolved_parser)
    augment_resolved_parser.add_argument("--run-root", type=Path, required=True)
    augment_resolved_parser.add_argument("--out-root", type=Path, required=True)
    augment_resolved_parser.add_argument("--split", default="test")
    augment_resolved_parser.add_argument("--dataset-dir", type=Path, default=DATASET_DIR)
    augment_resolved_parser.add_argument("--case-id", action="append", default=[])
    augment_resolved_parser.add_argument("--source", default="trajectory")
    augment_resolved_parser.add_argument("--changed-only", action="store_true")
    augment_resolved_parser.add_argument("--out-json", type=Path)
    augment_resolved_parser.set_defaults(func=augment_resolved_graphs_command)

    select_parser = subparsers.add_parser("select")
    _add_graph_root_args(select_parser)
    select_parser.add_argument("--baseline", type=Path, default=DEFAULT_CURRENT_BEST)
    _add_candidate_args(select_parser)
    select_parser.add_argument("--case-id", action="append", default=[])
    select_parser.add_argument("--leaderboard", type=Path)
    select_parser.add_argument("--team-name", default="隐元玩一玩")
    select_parser.add_argument("--skip-probed-cases", action="store_true")
    select_parser.add_argument("--split", default="test")
    select_parser.add_argument("--out-result", type=Path, required=True)
    select_parser.add_argument("--out-audit", type=Path, required=True)
    select_parser.add_argument("--min-support", type=float, default=0.58)
    select_parser.add_argument("--min-margin", type=float, default=0.08)
    select_parser.add_argument("--min-modalities", type=int, default=2)
    select_parser.add_argument("--max-novelty", type=float, default=0.62)
    select_parser.add_argument("--evidence-limit", type=int, default=32)
    select_parser.add_argument("--hypothesis-limit", type=int, default=10)
    select_parser.add_argument("--support-limit", type=int, default=4)
    select_parser.add_argument("--fail-on-missing-graph", action="store_true")
    select_parser.add_argument("--model-name", default="graph-ontology-evidence-bundle-verifier")
    select_parser.add_argument(
        "--agent-description",
        default=(
            "Graph/ontology evidence bundle verifier: conservatively keeps the current "
            "best answer unless a candidate is better supported by typed graph evidence. "
            "Hidden test references are not read."
        ),
    )
    select_parser.set_defaults(func=select_command)

    validation_parser = subparsers.add_parser("score-validation")
    validation_parser.add_argument("result", type=Path)
    validation_parser.add_argument("--dataset-dir", type=Path, default=DATASET_DIR)
    validation_parser.add_argument("--out", type=Path)
    validation_parser.add_argument("--show-cases", type=int, default=10)
    validation_parser.set_defaults(func=score_validation_command)

    synth_parser = subparsers.add_parser("synthesize")
    _add_graph_root_args(synth_parser)
    synth_parser.add_argument("--split", default="test")
    synth_parser.add_argument("--dataset-dir", type=Path, default=DATASET_DIR)
    synth_parser.add_argument("--case-id", action="append", default=[])
    synth_parser.add_argument("--out-result", type=Path, required=True)
    synth_parser.add_argument("--source-name", default="ontology-synth-v1")
    synth_parser.add_argument("--evidence-limit", type=int, default=32)
    synth_parser.add_argument("--hypothesis-limit", type=int, default=10)
    synth_parser.add_argument("--support-limit", type=int, default=4)
    synth_parser.add_argument("--model-name", default="graph-ontology-synth-v1")
    synth_parser.add_argument(
        "--agent-description",
        default=(
            "Deterministic graph/ontology evidence-bundle synthesis baseline. "
            "Uses visible graph evidence only and reads no hidden test references."
        ),
    )
    synth_parser.set_defaults(func=synthesize_command)

    triage_parser = subparsers.add_parser("triage")
    _add_graph_root_args(triage_parser)
    triage_parser.add_argument("--baseline", type=Path, default=DEFAULT_CURRENT_BEST)
    _add_candidate_args(triage_parser)
    triage_parser.add_argument("--case-id", action="append", default=[])
    triage_parser.add_argument("--split", default="test")
    triage_parser.add_argument("--dataset-dir", type=Path, default=DATASET_DIR)
    triage_parser.add_argument("--leaderboard", type=Path)
    triage_parser.add_argument("--team-name", default="隐元玩一玩")
    triage_parser.add_argument("--out-json", type=Path, required=True)
    triage_parser.add_argument("--out-md", type=Path)
    triage_parser.add_argument("--markdown-limit", type=int, default=40)
    triage_parser.set_defaults(func=triage_command)

    gaps_parser = subparsers.add_parser("coverage-gaps")
    _add_graph_root_args(gaps_parser)
    gaps_parser.add_argument("--baseline", type=Path, default=DEFAULT_CURRENT_BEST)
    _add_candidate_args(gaps_parser)
    gaps_parser.add_argument("--case-id", action="append", default=[])
    gaps_parser.add_argument("--split", default="test")
    gaps_parser.add_argument("--dataset-dir", type=Path, default=DATASET_DIR)
    gaps_parser.add_argument("--leaderboard", type=Path)
    gaps_parser.add_argument("--team-name", default="隐元玩一玩")
    gaps_parser.add_argument("--out-json", type=Path, required=True)
    gaps_parser.add_argument("--out-md", type=Path)
    gaps_parser.add_argument("--markdown-limit", type=int, default=50)
    gaps_parser.set_defaults(func=coverage_gaps_command)

    analogue_parser = subparsers.add_parser("case-analogues")
    _add_graph_root_args(analogue_parser)
    analogue_parser.add_argument("--baseline", type=Path, default=DEFAULT_CURRENT_BEST)
    analogue_parser.add_argument("--case-id", action="append", default=[])
    analogue_parser.add_argument("--split", default="test")
    analogue_parser.add_argument("--dataset-dir", type=Path, default=DATASET_DIR)
    analogue_parser.add_argument(
        "--validation-memory", type=Path, default=DEFAULT_VALIDATION_MEMORY
    )
    analogue_parser.add_argument("--leaderboard", type=Path)
    analogue_parser.add_argument("--team-name", default="隐元玩一玩")
    analogue_parser.add_argument("--match-limit", type=int, default=3)
    analogue_parser.add_argument("--out-json", type=Path, required=True)
    analogue_parser.add_argument("--out-md", type=Path)
    analogue_parser.add_argument("--markdown-limit", type=int, default=50)
    analogue_parser.set_defaults(func=case_analogues_command)

    raw_inventory_parser = subparsers.add_parser("raw-inventory")
    _add_graph_root_args(raw_inventory_parser)
    raw_inventory_parser.add_argument("--baseline", type=Path, default=DEFAULT_CURRENT_BEST)
    raw_inventory_parser.add_argument("--case-id", action="append", default=[])
    raw_inventory_parser.add_argument("--split", default="test")
    raw_inventory_parser.add_argument("--dataset-dir", type=Path, default=DATASET_DIR)
    raw_inventory_parser.add_argument("--leaderboard", type=Path)
    raw_inventory_parser.add_argument("--team-name", default="隐元玩一玩")
    raw_inventory_parser.add_argument("--top-files-per-case", type=int, default=8)
    raw_inventory_parser.add_argument("--out-json", type=Path, required=True)
    raw_inventory_parser.add_argument("--out-md", type=Path)
    raw_inventory_parser.add_argument("--markdown-limit", type=int, default=50)
    raw_inventory_parser.set_defaults(func=raw_inventory_command)

    frontier_parser = subparsers.add_parser("frontier")
    _add_graph_root_args(frontier_parser)
    frontier_parser.add_argument("--baseline", type=Path, default=DEFAULT_CURRENT_BEST)
    frontier_parser.add_argument("--case-id", action="append", default=[])
    frontier_parser.add_argument("--split", default="test")
    frontier_parser.add_argument("--dataset-dir", type=Path, default=DATASET_DIR)
    frontier_parser.add_argument(
        "--validation-memory", type=Path, default=DEFAULT_VALIDATION_MEMORY
    )
    frontier_parser.add_argument("--leaderboard", type=Path)
    frontier_parser.add_argument("--team-name", default="隐元玩一玩")
    frontier_parser.add_argument("--tomography", type=Path)
    frontier_parser.add_argument("--results-dir", type=Path, default=REALRCA_DMA)
    frontier_parser.add_argument("--reference-agent-name", default="")
    frontier_parser.add_argument("--top-files-per-case", type=int, default=8)
    frontier_parser.add_argument("--match-limit", type=int, default=3)
    frontier_parser.add_argument("--out-json", type=Path, required=True)
    frontier_parser.add_argument("--out-md", type=Path)
    frontier_parser.add_argument("--markdown-limit", type=int, default=60)
    frontier_parser.set_defaults(func=frontier_command)

    boundary_parser = subparsers.add_parser("boundary-deltas")
    _add_graph_root_args(boundary_parser)
    boundary_parser.add_argument("--baseline", type=Path, default=DEFAULT_CURRENT_BEST)
    boundary_parser.add_argument("--case-id", action="append", default=[])
    boundary_parser.add_argument("--split", default="test")
    boundary_parser.add_argument("--dataset-dir", type=Path, default=DATASET_DIR)
    boundary_parser.add_argument("--leaderboard", type=Path)
    boundary_parser.add_argument("--team-name", default="隐元玩一玩")
    boundary_parser.add_argument("--out-json", type=Path, required=True)
    boundary_parser.add_argument("--out-md", type=Path)
    boundary_parser.add_argument("--markdown-limit", type=int, default=50)
    boundary_parser.set_defaults(func=boundary_deltas_command)

    tomography_parser = subparsers.add_parser("tomography")
    tomography_parser.add_argument("--leaderboard", type=Path, required=True)
    tomography_parser.add_argument("--reference", type=Path, default=DEFAULT_CURRENT_BEST)
    tomography_parser.add_argument("--results-dir", type=Path, action="append", default=[])
    tomography_parser.add_argument("--team-name", default="隐元玩一玩")
    tomography_parser.add_argument("--reference-agent-name", default="")
    tomography_parser.add_argument("--out-json", type=Path, required=True)
    tomography_parser.add_argument("--out-md", type=Path)
    tomography_parser.add_argument("--markdown-limit", type=int, default=40)
    tomography_parser.set_defaults(func=tomography_command)

    calibrate_parser = subparsers.add_parser("calibrate")
    _add_graph_root_args(calibrate_parser)
    calibrate_parser.add_argument("--split", default="validation")
    calibrate_parser.add_argument("--dataset-dir", type=Path, default=DATASET_DIR)
    calibrate_parser.add_argument("--hypothesis-limit", type=int, default=10)
    calibrate_parser.add_argument("--min-overlap", type=int, default=2)
    calibrate_parser.add_argument("--min-recall", type=float, default=0.08)
    calibrate_parser.add_argument("--out-json", type=Path, required=True)
    calibrate_parser.add_argument("--out-md", type=Path)
    calibrate_parser.add_argument("--markdown-limit", type=int, default=40)
    calibrate_parser.set_defaults(func=calibrate_command)

    selector_calibration_parser = subparsers.add_parser("selector-calibration")
    _add_graph_root_args(selector_calibration_parser)
    _add_candidate_args(selector_calibration_parser)
    selector_calibration_parser.add_argument("--baseline", type=Path)
    selector_calibration_parser.add_argument("--split", default="validation")
    selector_calibration_parser.add_argument("--dataset-dir", type=Path, default=DATASET_DIR)
    selector_calibration_parser.add_argument("--evidence-limit", type=int, default=32)
    selector_calibration_parser.add_argument("--hypothesis-limit", type=int, default=10)
    selector_calibration_parser.add_argument("--support-limit", type=int, default=4)
    selector_calibration_parser.add_argument("--out-json", type=Path, required=True)
    selector_calibration_parser.add_argument("--out-md", type=Path)
    selector_calibration_parser.add_argument("--markdown-limit", type=int, default=60)
    selector_calibration_parser.set_defaults(func=selector_calibration_command)

    contract_gaps_parser = subparsers.add_parser("contract-gaps")
    contract_gaps_parser.add_argument("--analogue", type=Path, required=True)
    contract_gaps_parser.add_argument("--baseline", type=Path, default=DEFAULT_CURRENT_BEST)
    contract_gaps_parser.add_argument("--score-boundary", type=Path)
    contract_gaps_parser.add_argument("--dataset-dir", type=Path, default=DATASET_DIR)
    contract_gaps_parser.add_argument("--match-limit", type=int, default=3)
    contract_gaps_parser.add_argument("--min-similarity", type=float, default=0.78)
    contract_gaps_parser.add_argument("--item-coverage-threshold", type=float, default=0.18)
    contract_gaps_parser.add_argument("--out-json", type=Path, required=True)
    contract_gaps_parser.add_argument("--out-md", type=Path)
    contract_gaps_parser.add_argument("--markdown-limit", type=int, default=60)
    contract_gaps_parser.set_defaults(func=contract_gaps_command)

    repair_parser = subparsers.add_parser("repair-traces")
    _add_graph_root_args(repair_parser)
    repair_parser.add_argument("--baseline", type=Path, default=DEFAULT_CURRENT_BEST)
    repair_parser.add_argument("--case-id", action="append", default=[])
    repair_parser.add_argument("--split", default="test")
    repair_parser.add_argument("--out-result", type=Path, required=True)
    repair_parser.add_argument("--out-audit", type=Path, required=True)
    repair_parser.add_argument("--fail-on-missing-graph", action="store_true")
    repair_parser.add_argument(
        "--allow-inferred-trace",
        action="store_true",
        help="Experimentally replace invalid trace ids with matching graph traces not mentioned in the answer.",
    )
    repair_parser.add_argument("--model-name", default="graph-ontology-trace-repair-v1")
    repair_parser.add_argument(
        "--agent-description",
        default=(
            "Graph/ontology trace-id repair pass. Preserves diagnosis text and only replaces "
            "synthetic trace ids with visible graph-supported trace span ids. Hidden test "
            "references are not read."
        ),
    )
    repair_parser.set_defaults(func=repair_traces_command)

    enrich_parser = subparsers.add_parser("enrich-trajectories")
    _add_graph_root_args(enrich_parser)
    enrich_parser.add_argument("--baseline", type=Path, default=DEFAULT_CURRENT_BEST)
    enrich_parser.add_argument("--audit", type=Path, required=True)
    enrich_parser.add_argument("--leaderboard", type=Path)
    enrich_parser.add_argument("--team-name", default="隐元玩一玩")
    enrich_parser.add_argument("--skip-probed-cases", action="store_true")
    enrich_parser.add_argument("--case-id", action="append", default=[])
    enrich_parser.add_argument("--split", default="test")
    enrich_parser.add_argument("--out-result", type=Path, required=True)
    enrich_parser.add_argument("--out-audit", type=Path, required=True)
    enrich_parser.add_argument("--max-terms", type=int, default=3)
    enrich_parser.add_argument("--max-answer-chars", type=int, default=1200)
    enrich_parser.add_argument("--min-term-score", type=int, default=18)
    enrich_parser.add_argument("--evidence-limit", type=int, default=32)
    enrich_parser.add_argument("--hypothesis-limit", type=int, default=10)
    enrich_parser.add_argument("--support-limit", type=int, default=4)
    enrich_parser.add_argument("--fail-on-missing-graph", action="store_true")
    enrich_parser.add_argument("--model-name", default="graph-ontology-trajectory-enrichment-v1")
    enrich_parser.add_argument(
        "--agent-description",
        default=(
            "Graph/ontology trajectory evidence enrichment: preserves the current best "
            "root-cause answer and trace id, and only appends visible trajectory terms "
            "that are graph-supported and aligned with baseline root entities. Hidden "
            "test references are not read."
        ),
    )
    enrich_parser.set_defaults(func=enrich_trajectories_command)

    generate_parser = subparsers.add_parser("generate-candidates")
    _add_graph_root_args(generate_parser)
    generate_parser.add_argument("--baseline", type=Path, default=DEFAULT_CURRENT_BEST)
    _add_candidate_args(generate_parser)
    generate_parser.add_argument("--case-id", action="append", required=True)
    generate_parser.add_argument("--split", default="test")
    generate_parser.add_argument("--dataset-dir", type=Path, default=DATASET_DIR)
    generate_parser.add_argument("--leaderboard", type=Path)
    generate_parser.add_argument("--team-name", default="隐元玩一玩")
    generate_parser.add_argument("--skip-probed-cases", action="store_true")
    generate_parser.add_argument("--frontier", type=Path)
    generate_parser.add_argument("--graph-analogue-report", type=Path)
    generate_parser.add_argument("--out-result", type=Path, required=True)
    generate_parser.add_argument("--out-audit", type=Path, required=True)
    generate_parser.add_argument(
        "--out-dir", type=Path, default=Path(".bench-results/realrca-graph/runs")
    )
    generate_parser.add_argument("--run-label", default="evidence-candidate-gen-v1")
    generate_parser.add_argument("--candidate-limit", type=int, default=5)
    generate_parser.add_argument("--answer-chars", type=int, default=700)
    generate_parser.add_argument("--strategy-hint", default="")
    generate_parser.add_argument("--trajectory-audit", type=Path, action="append", default=[])
    generate_parser.add_argument("--trajectory-term-limit", type=int, default=8)
    generate_parser.add_argument(
        "--validation-memory", type=Path, default=DEFAULT_VALIDATION_MEMORY
    )
    generate_parser.add_argument("--validation-exemplar-limit", type=int, default=3)
    generate_parser.add_argument("--evidence-limit", type=int, default=32)
    generate_parser.add_argument("--hypothesis-limit", type=int, default=10)
    generate_parser.add_argument("--support-limit", type=int, default=4)
    generate_parser.add_argument("--dry-run", action="store_true")
    generate_parser.add_argument("--rerun", action="store_true")
    generate_parser.add_argument("--concurrency", type=int, default=2)
    generate_parser.add_argument("--timeout-sec", type=int, default=900)
    generate_parser.add_argument("--dma-command-timeout-sec", type=int, default=180)
    generate_parser.add_argument("--dma-api-retries", type=int, default=5)
    generate_parser.add_argument("--poll-interval-sec", type=float, default=3.0)
    generate_parser.add_argument("--dma-bin", type=Path, default=DMA_BIN)
    generate_parser.add_argument("--agent-id", default="change-detect:realrca-verifier-20260826")
    generate_parser.add_argument("--env-id", default="01KXZG5SMGFQV7JGMKAZKRNDB4")
    generate_parser.add_argument("--llm-config-id", default="llm_01KVYRP83M8WFS56ANYRP40ZQ7")
    generate_parser.add_argument(
        "--model-name", default="dma/deepseek-v4-pro+ontology-evidence-generator"
    )
    generate_parser.add_argument("--fail-on-generation-error", action="store_true")
    generate_parser.add_argument(
        "--agent-description",
        default=(
            "DMA ontology/evidence-bundle candidate generator: creates partial candidate "
            "answers from visible RealRCA case fields, current best baseline, candidate "
            "summaries, and typed graph evidence. Hidden test references are not read."
        ),
    )
    generate_parser.set_defaults(func=generate_candidates_command)

    verify_parser = subparsers.add_parser("verify-candidates")
    _add_graph_root_args(verify_parser)
    verify_parser.add_argument("--baseline", type=Path, default=DEFAULT_CURRENT_BEST)
    _add_candidate_args(verify_parser)
    verify_parser.add_argument("--case-id", action="append", default=[])
    verify_parser.add_argument("--split", default="test")
    verify_parser.add_argument("--dataset-dir", type=Path, default=DATASET_DIR)
    verify_parser.add_argument("--leaderboard", type=Path)
    verify_parser.add_argument("--team-name", default="隐元玩一玩")
    verify_parser.add_argument("--out-result", type=Path, required=True)
    verify_parser.add_argument("--out-audit", type=Path, required=True)
    verify_parser.add_argument(
        "--out-dir", type=Path, default=Path(".bench-results/realrca-graph/runs")
    )
    verify_parser.add_argument("--run-label", default="pairwise-verifier-v1")
    verify_parser.add_argument("--candidate-limit", type=int, default=2)
    verify_parser.add_argument("--min-confidence", type=float, default=0.72)
    verify_parser.add_argument("--min-support-margin", type=float, default=0.05)
    verify_parser.add_argument("--strategy-hint", default="")
    verify_parser.add_argument("--evidence-limit", type=int, default=32)
    verify_parser.add_argument("--hypothesis-limit", type=int, default=10)
    verify_parser.add_argument("--support-limit", type=int, default=4)
    verify_parser.add_argument("--dry-run", action="store_true")
    verify_parser.add_argument("--rerun", action="store_true")
    verify_parser.add_argument("--concurrency", type=int, default=2)
    verify_parser.add_argument("--timeout-sec", type=int, default=900)
    verify_parser.add_argument("--dma-command-timeout-sec", type=int, default=180)
    verify_parser.add_argument("--dma-api-retries", type=int, default=5)
    verify_parser.add_argument("--poll-interval-sec", type=float, default=3.0)
    verify_parser.add_argument("--dma-bin", type=Path, default=DMA_BIN)
    verify_parser.add_argument("--agent-id", default="change-detect:realrca-verifier-20260826")
    verify_parser.add_argument("--env-id", default="01KXZG5SMGFQV7JGMKAZKRNDB4")
    verify_parser.add_argument("--llm-config-id", default="llm_01KVYRP83M8WFS56ANYRP40ZQ7")
    verify_parser.add_argument(
        "--model-name", default="dma/deepseek-v4-pro+pairwise-evidence-verifier"
    )
    verify_parser.add_argument("--fail-on-verifier-error", action="store_true")
    verify_parser.add_argument(
        "--agent-description",
        default=(
            "DMA pairwise evidence verifier: compares a challenger answer with the "
            "current best answer using only visible case fields and typed ontology "
            "evidence bundles, then applies deterministic hard-risk gates before "
            "selecting replacements. Hidden test references are not read."
        ),
    )
    verify_parser.set_defaults(func=verify_candidates_command)

    index_parser = subparsers.add_parser("index-graphs")
    _add_graph_root_args(index_parser)
    index_parser.add_argument("--split", default="test")
    index_parser.add_argument("--db", type=Path, default=DEFAULT_GRAPH_DB)
    index_parser.add_argument(
        "--resolved-label",
        default="",
        help="Index only the first available graph_context per case under this graph label.",
    )
    index_parser.add_argument("--out-json", type=Path, required=True)
    index_parser.set_defaults(func=index_graphs_command)

    graph_analogues_parser = subparsers.add_parser("graph-analogues")
    graph_analogues_parser.add_argument("--split", default="test")
    graph_analogues_parser.add_argument("--db", type=Path, default=DEFAULT_GRAPH_DB)
    graph_analogues_parser.add_argument("--query-label", required=True)
    graph_analogues_parser.add_argument("--search-label", action="append", default=[])
    graph_analogues_parser.add_argument("--search-split", action="append", default=[])
    graph_analogues_parser.add_argument("--case-id", action="append", default=[])
    graph_analogues_parser.add_argument("--match-limit", type=int, default=5)
    graph_analogues_parser.add_argument("--leaderboard", type=Path)
    graph_analogues_parser.add_argument("--team-name", default="隐元玩一玩")
    graph_analogues_parser.add_argument("--out-json", type=Path, required=True)
    graph_analogues_parser.add_argument("--out-md", type=Path)
    graph_analogues_parser.add_argument("--markdown-limit", type=int, default=60)
    graph_analogues_parser.set_defaults(func=graph_analogues_command)

    answer_outliers_parser = subparsers.add_parser("answer-outliers")
    answer_outliers_parser.add_argument("--baseline", type=Path, default=DEFAULT_CURRENT_BEST)
    answer_outliers_parser.add_argument("--internal-analogue", type=Path)
    answer_outliers_parser.add_argument("--public-analogue", type=Path)
    answer_outliers_parser.add_argument("--frontier", type=Path)
    answer_outliers_parser.add_argument("--case-id", action="append", default=[])
    answer_outliers_parser.add_argument("--out-json", type=Path, required=True)
    answer_outliers_parser.add_argument("--out-md", type=Path)
    answer_outliers_parser.add_argument("--markdown-limit", type=int, default=60)
    answer_outliers_parser.set_defaults(func=answer_outliers_command)

    score_boundaries_parser = subparsers.add_parser("score-boundaries")
    score_boundaries_parser.add_argument("--baseline", type=Path, default=DEFAULT_CURRENT_BEST)
    score_boundaries_parser.add_argument("--frontier", type=Path)
    score_boundaries_parser.add_argument("--tomography", type=Path)
    score_boundaries_parser.add_argument("--answer-outlier", type=Path)
    score_boundaries_parser.add_argument("--case-id", action="append", default=[])
    score_boundaries_parser.add_argument("--out-json", type=Path, required=True)
    score_boundaries_parser.add_argument("--out-md", type=Path)
    score_boundaries_parser.add_argument("--markdown-limit", type=int, default=60)
    score_boundaries_parser.set_defaults(func=score_boundaries_command)

    path_frontier_parser = subparsers.add_parser("path-frontier")
    _add_graph_root_args(path_frontier_parser)
    path_frontier_parser.add_argument("--baseline", type=Path, default=DEFAULT_CURRENT_BEST)
    path_frontier_parser.add_argument("--split", default="test")
    path_frontier_parser.add_argument("--case-id", action="append", default=[])
    path_frontier_parser.add_argument("--out-json", type=Path, required=True)
    path_frontier_parser.add_argument("--out-md", type=Path)
    path_frontier_parser.add_argument("--evidence-limit", type=int, default=32)
    path_frontier_parser.add_argument("--hypothesis-limit", type=int, default=10)
    path_frontier_parser.add_argument("--support-limit", type=int, default=4)
    path_frontier_parser.add_argument("--max-depth", type=int, default=5)
    path_frontier_parser.add_argument("--seed-limit", type=int, default=8)
    path_frontier_parser.add_argument("--markdown-limit", type=int, default=60)
    path_frontier_parser.set_defaults(func=path_frontier_command)

    pipeline_parser = subparsers.add_parser("pipeline-status")
    pipeline_parser.add_argument("--leaderboard", type=Path)
    pipeline_parser.add_argument("--baseline", type=Path, default=DEFAULT_CURRENT_BEST)
    pipeline_parser.add_argument("--selector-audit", type=Path)
    pipeline_parser.add_argument("--score-boundary", type=Path)
    pipeline_parser.add_argument("--tomography", type=Path)
    pipeline_parser.add_argument("--team-name", default="隐元玩一玩")
    pipeline_parser.add_argument("--target-accuracy", type=float, default=90.0)
    pipeline_parser.add_argument("--out-json", type=Path, required=True)
    pipeline_parser.add_argument("--out-md", type=Path)
    pipeline_parser.set_defaults(func=pipeline_status_command)

    search_parser = subparsers.add_parser("search-graph")
    search_parser.add_argument("text")
    search_parser.add_argument("--split", default="test")
    search_parser.add_argument("--case-id", default="")
    search_parser.add_argument("--graph-label", action="append", default=[])
    search_parser.add_argument("--kind", action="append", default=[])
    search_parser.add_argument("--limit", type=int, default=20)
    search_parser.add_argument("--db", type=Path, default=DEFAULT_GRAPH_DB)
    search_parser.add_argument("--out-json", type=Path)
    search_parser.set_defaults(func=search_graph_command)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
