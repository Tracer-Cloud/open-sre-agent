from __future__ import annotations

from collections import Counter
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from tests.benchmarks.realrca_graph.io import DEFAULT_CURRENT_BEST, load_json
from tests.benchmarks.realrca_graph.probe_feedback import ProbeFeedbackLedger

_COMPONENT_FILES = {
    "graph_context_ingestion": "bundle.py",
    "ontology_graph": "ontology_graph.py",
    "graph_store": "graph_store.py",
    "evidence_bundle_cache": "bundle_cache.py",
    "deterministic_verifier": "verifier.py",
    "dma_candidate_generation": "generation.py",
    "llm_pairwise_verifier": "llm_verifier.py",
    "frontier_analysis": "frontier.py",
    "score_tomography": "score_tomography.py",
    "score_boundaries": "score_boundaries.py",
    "public_contract_gaps": "contract_gaps.py",
    "anchored_synthesis": "synthesis.py",
}


@dataclass(frozen=True)
class LeaderboardPosition:
    """Current public leaderboard position for one team."""

    team_name: str
    agent_name: str
    accuracy: float | None
    coverage: float | None
    quality_score: float | None
    submitted_at: str
    model_name: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class PipelineStatus:
    """Auditable status for the graph/ontology benchmark iteration loop."""

    target_accuracy: float
    current_best: LeaderboardPosition
    score_gap: float | None
    baseline_path: str
    leaderboard_path: str
    selector_audit_path: str
    score_boundary_path: str
    tomography_path: str
    framework_ready: bool
    component_status: dict[str, bool]
    ready_to_submit: bool
    next_action: str
    selector_summary: dict[str, Any]
    score_boundary_summary: dict[str, Any]
    tomography_summary: dict[str, Any]
    probe_feedback_summary: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return {
            "hidden_test_reference_used": False,
            "target_accuracy": self.target_accuracy,
            "current_best": self.current_best.to_dict(),
            "score_gap": self.score_gap,
            "baseline_path": self.baseline_path,
            "leaderboard_path": self.leaderboard_path,
            "selector_audit_path": self.selector_audit_path,
            "score_boundary_path": self.score_boundary_path,
            "tomography_path": self.tomography_path,
            "framework_ready": self.framework_ready,
            "component_status": dict(self.component_status),
            "ready_to_submit": self.ready_to_submit,
            "next_action": self.next_action,
            "selector_summary": self.selector_summary,
            "score_boundary_summary": self.score_boundary_summary,
            "tomography_summary": self.tomography_summary,
            "probe_feedback_summary": self.probe_feedback_summary,
        }


def build_pipeline_status(
    *,
    leaderboard_path: Path | None,
    team_name: str,
    baseline_path: Path = DEFAULT_CURRENT_BEST,
    selector_audit_path: Path | None = None,
    score_boundary_path: Path | None = None,
    tomography_path: Path | None = None,
    target_accuracy: float = 90.0,
) -> PipelineStatus:
    """Summarize the current graph/ontology iteration state without running probes."""

    leaderboard_payload = _load_optional_dict(leaderboard_path)
    current_best = _leaderboard_position(leaderboard_payload, team_name=team_name)
    selector_summary = _selector_summary(_load_optional_dict(selector_audit_path))
    score_boundary_summary = _score_boundary_summary(_load_optional_dict(score_boundary_path))
    tomography_summary = _tomography_summary(_load_optional_dict(tomography_path))
    feedback_summary = _probe_feedback_summary(leaderboard_payload, team_name=team_name)
    component_status = _component_status()
    ready_to_submit = bool(selector_summary.get("accepted_replacements"))
    score_gap = (
        round(target_accuracy - current_best.accuracy, 4)
        if current_best.accuracy is not None
        else None
    )
    return PipelineStatus(
        target_accuracy=target_accuracy,
        current_best=current_best,
        score_gap=score_gap,
        baseline_path=str(baseline_path),
        leaderboard_path=str(leaderboard_path or ""),
        selector_audit_path=str(selector_audit_path or ""),
        score_boundary_path=str(score_boundary_path or ""),
        tomography_path=str(tomography_path or ""),
        framework_ready=all(component_status.values()),
        component_status=component_status,
        ready_to_submit=ready_to_submit,
        next_action=_next_action(
            current_accuracy=current_best.accuracy,
            target_accuracy=target_accuracy,
            ready_to_submit=ready_to_submit,
            selector_summary=selector_summary,
            score_boundary_summary=score_boundary_summary,
            tomography_summary=tomography_summary,
        ),
        selector_summary=selector_summary,
        score_boundary_summary=score_boundary_summary,
        tomography_summary=tomography_summary,
        probe_feedback_summary=feedback_summary,
    )


def render_pipeline_status_markdown(status: PipelineStatus) -> str:
    """Render a compact operator-facing status report."""

    best = status.current_best
    lines = [
        "# RealRCA Graph Pipeline Status",
        "",
        "- hidden_test_reference_used: `False`",
        f"- target_accuracy: `{status.target_accuracy}`",
        f"- current_best: `{_fmt(best.accuracy)}` via `{best.agent_name or '-'}`",
        f"- coverage: `{_fmt(best.coverage)}`; quality_score: `{_fmt(best.quality_score)}`",
        f"- score_gap: `{_fmt(status.score_gap)}`",
        f"- framework_ready: `{status.framework_ready}`",
        f"- ready_to_submit: `{status.ready_to_submit}`",
        f"- next_action: {status.next_action}",
        "",
        "## Selector",
        "",
        f"- audit: `{status.selector_audit_path}`",
        f"- selected_cases: `{status.selector_summary.get('selected_case_count', 0)}`",
        f"- accepted_replacements: `{status.selector_summary.get('accepted_replacements', [])}`",
        f"- top_candidate_risks: `{status.selector_summary.get('top_candidate_risks', {})}`",
        "",
        "## Score Boundary",
        "",
        f"- report: `{status.score_boundary_path}`",
        f"- actions: `{status.score_boundary_summary.get('action_counts', {})}`",
        f"- top_cases: `{status.score_boundary_summary.get('top_cases', [])}`",
        "",
        "## Feedback",
        "",
        f"- tomography_positive: `{status.tomography_summary.get('positive_answer_count')}`",
        f"- tomography_negative: `{status.tomography_summary.get('negative_estimate_count')}`",
        f"- public_probe_outcomes: `{status.probe_feedback_summary.get('outcomes', {})}`",
    ]
    return "\n".join(lines).rstrip() + "\n"


def _load_optional_dict(path: Path | None) -> dict[str, Any]:
    if path is None or not path.exists():
        return {}
    payload = load_json(path)
    return payload if isinstance(payload, dict) else {}


def _leaderboard_position(payload: dict[str, Any], *, team_name: str) -> LeaderboardPosition:
    rows = [
        item
        for item in payload.get("items", [])
        if isinstance(item, dict) and item.get("team_name") == team_name
    ]
    rows.sort(key=lambda item: _float(item.get("accuracy")), reverse=True)
    best = rows[0] if rows else {}
    return LeaderboardPosition(
        team_name=team_name,
        agent_name=str(best.get("agent_name") or ""),
        accuracy=_none_float(best.get("accuracy")),
        coverage=_none_float(best.get("coverage")),
        quality_score=_none_float(best.get("quality_score")),
        submitted_at=str(best.get("submitted_at") or ""),
        model_name=str(best.get("model_name") or best.get("model") or ""),
    )


def _selector_summary(payload: dict[str, Any]) -> dict[str, Any]:
    decisions = _list(payload.get("decisions"))
    risk_counts: Counter[str] = Counter()
    for decision in decisions:
        if not isinstance(decision, dict):
            continue
        baseline_source = str(decision.get("baseline_source") or "")
        for score in _list(decision.get("scores")):
            if not isinstance(score, dict) or score.get("source") == baseline_source:
                continue
            risk_counts.update(_str_list(score.get("risk_flags")))
    return {
        "case_count": int(payload.get("case_count") or 0),
        "selected_case_count": int(payload.get("selected_case_count") or len(decisions)),
        "candidate_file_count": len(_list(payload.get("candidate_files"))),
        "accepted_replacements": _str_list(payload.get("accepted_replacements")),
        "missing_graph_count": len(_list(payload.get("missing_graphs"))),
        "top_candidate_risks": dict(risk_counts.most_common(8)),
    }


def _score_boundary_summary(payload: dict[str, Any]) -> dict[str, Any]:
    cases = [item for item in _list(payload.get("cases")) if isinstance(item, dict)]
    top_cases = [
        {
            "case_suffix": str(item.get("case_suffix") or ""),
            "action": str(item.get("action") or ""),
            "priority_score": _float(item.get("priority_score")),
            "categories": _str_list(item.get("categories"))[:4],
        }
        for item in cases[:8]
    ]
    return {
        "case_count": int(payload.get("case_count") or len(cases)),
        "action_counts": _dict(payload.get("action_counts")),
        "category_counts": _dict(payload.get("category_counts")),
        "candidate_action_count": sum(
            1
            for item in cases
            if str(item.get("action") or "")
            in {"generate_candidate", "generate_boundary_challenger"}
        ),
        "top_cases": top_cases,
    }


def _tomography_summary(payload: dict[str, Any]) -> dict[str, Any]:
    cases = [item for item in _list(payload.get("cases")) if isinstance(item, dict)]
    negative_estimates = 0
    zero_estimates = 0
    for case in cases:
        for estimate in _list(case.get("estimates")):
            if not isinstance(estimate, dict):
                continue
            value = _float(estimate.get("estimate"))
            if value < -0.05:
                negative_estimates += 1
            elif abs(value) <= 0.05:
                zero_estimates += 1
    return {
        "reference_accuracy": _none_float(payload.get("reference_accuracy")),
        "matched_submission_count": int(payload.get("matched_submission_count") or 0),
        "inferred_answer_count": int(payload.get("inferred_answer_count") or 0),
        "positive_answer_count": int(payload.get("positive_answer_count") or 0),
        "negative_estimate_count": negative_estimates,
        "zero_estimate_count": zero_estimates,
    }


def _probe_feedback_summary(payload: dict[str, Any], *, team_name: str) -> dict[str, Any]:
    if not payload:
        return {"case_count": 0, "outcomes": {}}
    ledger = ProbeFeedbackLedger.from_leaderboard(payload, team_name=team_name)
    return {
        "case_count": len(ledger.cases),
        "reference_accuracy": ledger.reference_accuracy,
        "outcomes": ledger.to_dict()["outcomes"],
    }


def _component_status() -> dict[str, bool]:
    package_dir = Path(__file__).resolve().parent
    return {
        name: (package_dir / file_name).exists() for name, file_name in _COMPONENT_FILES.items()
    }


def _next_action(
    *,
    current_accuracy: float | None,
    target_accuracy: float,
    ready_to_submit: bool,
    selector_summary: dict[str, Any],
    score_boundary_summary: dict[str, Any],
    tomography_summary: dict[str, Any],
) -> str:
    if current_accuracy is not None and current_accuracy >= target_accuracy:
        return "write_yuque_report_and_freeze_successful_pipeline"
    if ready_to_submit:
        return "submit_selector_result_then_refresh_leaderboard"
    if int(tomography_summary.get("positive_answer_count") or 0) > 0:
        return "replay_positive_tomography_variant_with_current_verifier"
    if int(score_boundary_summary.get("candidate_action_count") or 0) > 0:
        return "generate_only_root_boundary_challengers_for_unblocked_cases"
    if int(selector_summary.get("selected_case_count") or 0) and not selector_summary.get(
        "accepted_replacements"
    ):
        return "mine_new_observability_sources_or_root_boundary_evidence_before_more_dma"
    return "build_or_refresh_frontier_score_boundary_reports"


def _fmt(value: float | None) -> str:
    return "-" if value is None else f"{value:.4g}"


def _none_float(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _float(value: Any) -> float:
    numeric = _none_float(value)
    return numeric if numeric is not None else 0.0


def _list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _str_list(value: Any) -> list[str]:
    return [str(item) for item in _list(value) if str(item)]


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}
