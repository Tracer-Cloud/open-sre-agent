from __future__ import annotations

import re
from collections import Counter
from collections.abc import Sequence
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from tests.benchmarks.realrca_graph.boundary_analysis import (
    BoundaryDeltaCase,
    build_boundary_delta_report,
)
from tests.benchmarks.realrca_graph.case_analogues import (
    CaseAnalogue,
    build_case_analogue_report,
)
from tests.benchmarks.realrca_graph.features import clip_text
from tests.benchmarks.realrca_graph.io import (
    DATASET_DIR,
    DEFAULT_CURRENT_BEST,
    REALRCA_DMA,
    load_json,
    rows_by_case,
)
from tests.benchmarks.realrca_graph.mechanism_terms import (
    ROOT_CHANGING_RAW_MECHANISMS,
    SOFT_RAW_MECHANISMS,
    baseline_excluded_mechanisms,
    baseline_negated_mechanisms_in_text,
    mechanism_markers,
)
from tests.benchmarks.realrca_graph.probe_feedback import (
    CaseProbeFeedback,
    ProbeFeedbackLedger,
    case_suffix,
)
from tests.benchmarks.realrca_graph.raw_inventory import (
    RawInventoryCase,
    build_raw_inventory_report,
)
from tests.benchmarks.realrca_graph.score_tomography import (
    build_tomography_report,
)

REAL_TRACE_ID_RE = re.compile(r"^[0-9a-f]{24,40}$", re.IGNORECASE)


@dataclass(frozen=True)
class FrontierCase:
    """One case ranked for the next non-hidden RealRCA improvement experiment."""

    case_id: str
    case_suffix: str
    case_type: str
    frontier_score: float
    bucket: str
    action: str
    signals: list[str]
    blockers: list[str]
    graph_path: str | None
    baseline_support: float
    baseline_risks: list[str]
    boundary_score: float
    boundary_categories: list[str]
    analogue_score: float
    analogue_categories: list[str]
    analogue_top_case: str
    analogue_similarity: float | None
    raw_score: float
    raw_uncovered_mechanisms: list[str]
    raw_categories: list[str]
    probe_count: int
    negative_probe_count: int
    best_probe_accuracy: float | None
    best_probe_delta: float | None
    tomography_best_estimate: float | None
    tomography_observations: int
    synthetic_trace_id: bool
    top_hypothesis: str
    baseline_preview: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class FrontierReport:
    """Aggregate frontier used to select graph/ontology/verifier experiments."""

    split: str
    baseline_path: str
    graph_roots: list[str]
    validation_memory_path: str
    case_count: int
    best_leaderboard_accuracy: float | None
    bucket_counts: dict[str, int]
    signal_counts: dict[str, int]
    blocker_counts: dict[str, int]
    cases: list[FrontierCase]

    def to_dict(self) -> dict[str, Any]:
        return {
            "split": self.split,
            "baseline_path": self.baseline_path,
            "graph_roots": list(self.graph_roots),
            "validation_memory_path": self.validation_memory_path,
            "public_validation_truth_used": True,
            "hidden_test_reference_used": False,
            "case_count": self.case_count,
            "best_leaderboard_accuracy": self.best_leaderboard_accuracy,
            "bucket_counts": dict(self.bucket_counts),
            "signal_counts": dict(self.signal_counts),
            "blocker_counts": dict(self.blocker_counts),
            "cases": [item.to_dict() for item in self.cases],
        }


def build_frontier_report(
    *,
    baseline_path: Path = DEFAULT_CURRENT_BEST,
    graph_roots: Sequence[Path] = (),
    split: str = "test",
    dataset_dir: Path = DATASET_DIR,
    validation_memory_path: Path,
    case_ids: Sequence[str] = (),
    leaderboard_path: Path | None = None,
    team_name: str = "隐元玩一玩",
    tomography_path: Path | None = None,
    results_dir: Path = REALRCA_DMA,
    reference_agent_name: str | None = None,
    top_files_per_case: int = 8,
    match_limit: int = 3,
) -> FrontierReport:
    """Rank next experiments by merging graph gaps, analogues, and public probe feedback."""

    baseline_rows = rows_by_case(baseline_path, source=baseline_path.stem)
    target_case_ids = _target_case_ids(baseline_rows.keys(), case_ids)
    raw_report = build_raw_inventory_report(
        baseline_path=baseline_path,
        graph_roots=graph_roots,
        split=split,
        dataset_dir=dataset_dir,
        case_ids=target_case_ids,
        leaderboard_path=leaderboard_path,
        team_name=team_name,
        top_files_per_case=top_files_per_case,
    )
    analogue_report = build_case_analogue_report(
        baseline_path=baseline_path,
        graph_roots=graph_roots,
        split=split,
        validation_memory_path=validation_memory_path,
        dataset_dir=dataset_dir,
        case_ids=target_case_ids,
        match_limit=match_limit,
        leaderboard_path=leaderboard_path,
        team_name=team_name,
    )
    boundary_report = build_boundary_delta_report(
        baseline_path=baseline_path,
        graph_roots=graph_roots,
        split=split,
        dataset_dir=dataset_dir,
        case_ids=target_case_ids,
        leaderboard_path=leaderboard_path,
        team_name=team_name,
    )
    ledger = _feedback_ledger(leaderboard_path, team_name)
    tomography = _tomography_by_case(
        leaderboard_path=leaderboard_path,
        baseline_path=baseline_path,
        tomography_path=tomography_path,
        results_dir=results_dir,
        team_name=team_name,
        reference_agent_name=reference_agent_name,
    )
    raw_by_case = {item.case_id: item for item in raw_report.cases}
    analogue_by_case = {item.case_id: item for item in analogue_report.cases}
    boundary_by_case = {item.case_id: item for item in boundary_report.cases}
    cases: list[FrontierCase] = []
    for case_id in target_case_ids:
        baseline = baseline_rows[case_id]
        raw = raw_by_case.get(case_id)
        analogue = analogue_by_case.get(case_id)
        boundary = boundary_by_case.get(case_id)
        feedback = ledger.for_case_id(case_id) if ledger is not None else None
        cases.append(
            _frontier_case(
                case_id=case_id,
                baseline_text=baseline.diagnosis_output,
                trace_id=baseline.trace_id,
                raw=raw,
                analogue=analogue,
                boundary=boundary,
                feedback=feedback,
                tomography=tomography.get(case_id) or tomography.get(case_suffix(case_id)),
            )
        )
    cases.sort(key=lambda item: (-item.frontier_score, item.bucket, item.case_id))
    return FrontierReport(
        split=split,
        baseline_path=str(baseline_path),
        graph_roots=[str(root) for root in graph_roots],
        validation_memory_path=str(validation_memory_path),
        case_count=len(cases),
        best_leaderboard_accuracy=_best_leaderboard_accuracy(ledger),
        bucket_counts=dict(Counter(item.bucket for item in cases)),
        signal_counts=dict(Counter(signal for item in cases for signal in item.signals)),
        blocker_counts=dict(Counter(blocker for item in cases for blocker in item.blockers)),
        cases=cases,
    )


def render_frontier_markdown(report: FrontierReport, *, limit: int = 60) -> str:
    """Render a compact frontier report for choosing the next DMA batch."""

    lines = [
        "# RealRCA Experiment Frontier",
        "",
        f"- split: `{report.split}`",
        f"- cases: `{report.case_count}`",
        f"- baseline: `{report.baseline_path}`",
        f"- graph_roots: `{report.graph_roots}`",
        f"- validation_memory: `{report.validation_memory_path}`",
        f"- best_leaderboard_accuracy: `{report.best_leaderboard_accuracy}`",
        "- public_validation_truth_used: `True`",
        "- hidden_test_reference_used: `False`",
        f"- buckets: `{_top_counts(report.bucket_counts)}`",
        f"- top_signals: `{_top_counts(report.signal_counts)}`",
        f"- top_blockers: `{_top_counts(report.blocker_counts)}`",
        "",
        "## Ranked Frontier",
        "",
        "| rank | case | type | score | bucket | support | probes | raw gaps | signals | blockers | action |",
        "| --- | --- | --- | ---: | --- | ---: | ---: | --- | --- | --- | --- |",
    ]
    for index, item in enumerate(report.cases[:limit], start=1):
        lines.append(
            "| "
            + " | ".join(
                [
                    str(index),
                    f"`{item.case_suffix}`",
                    item.case_type or "-",
                    f"{item.frontier_score:.3f}",
                    item.bucket,
                    f"{item.baseline_support:.4f}",
                    str(item.probe_count),
                    ",".join(item.raw_uncovered_mechanisms[:4]) or "-",
                    ",".join(item.signals[:4]) or "-",
                    ",".join(item.blockers[:4]) or "-",
                    _cell(item.action),
                ]
            )
            + " |"
        )
    lines.extend(["", "## Case Notes", ""])
    for item in report.cases[:limit]:
        lines.extend(
            [
                f"### `{item.case_suffix}` {item.case_type}",
                "",
                f"- case_id: `{item.case_id}`",
                f"- bucket: `{item.bucket}`; score: `{item.frontier_score}`",
                f"- graph_path: `{item.graph_path}`",
                f"- support: `{item.baseline_support}`; risks: `{item.baseline_risks}`",
                f"- boundary: score=`{item.boundary_score}` categories=`{item.boundary_categories}`",
                (
                    f"- analogue: score=`{item.analogue_score}` categories=`{item.analogue_categories}` "
                    f"top=`{item.analogue_top_case}` similarity=`{item.analogue_similarity}`"
                ),
                f"- raw: score=`{item.raw_score}` uncovered=`{item.raw_uncovered_mechanisms}` categories=`{item.raw_categories}`",
                (
                    f"- probes: count=`{item.probe_count}` negative=`{item.negative_probe_count}` "
                    f"best_accuracy=`{item.best_probe_accuracy}` best_delta=`{item.best_probe_delta}`"
                ),
                (
                    f"- tomography: best_estimate=`{item.tomography_best_estimate}` "
                    f"observations=`{item.tomography_observations}`"
                ),
                f"- synthetic_trace_id: `{item.synthetic_trace_id}`",
                f"- top_hypothesis: {item.top_hypothesis or '-'}",
                f"- signals: `{item.signals}`",
                f"- blockers: `{item.blockers}`",
                f"- action: {item.action}",
                f"- baseline_preview: {item.baseline_preview}",
                "",
            ]
        )
    return "\n".join(lines).rstrip() + "\n"


def _frontier_case(
    *,
    case_id: str,
    baseline_text: str,
    trace_id: str,
    raw: RawInventoryCase | None,
    analogue: CaseAnalogue | None,
    boundary: BoundaryDeltaCase | None,
    feedback: CaseProbeFeedback | None,
    tomography: dict[str, Any] | None,
) -> FrontierCase:
    signals: list[str] = []
    blockers: list[str] = []
    raw_uncovered = list(raw.uncovered_mechanisms if raw is not None else [])
    excluded_raw = baseline_excluded_mechanisms(baseline_text, raw_uncovered)
    actionable_raw_uncovered = [item for item in raw_uncovered if item not in excluded_raw]
    raw_root_changing = sorted(set(actionable_raw_uncovered) & ROOT_CHANGING_RAW_MECHANISMS)
    raw_soft_only = bool(actionable_raw_uncovered) and not raw_root_changing
    boundary_categories = list(boundary.categories if boundary is not None else [])
    analogue_categories = list(analogue.categories if analogue is not None else [])
    baseline_support = _first_float(
        boundary.baseline_support if boundary is not None else None,
        analogue.baseline_support if analogue is not None else None,
    )
    baseline_risks = _unique(
        list(boundary.baseline_risks if boundary is not None else [])
        + list(analogue.baseline_risks if analogue is not None else [])
    )
    score = 0.0
    top_hypothesis = _top_hypothesis(analogue, boundary)
    negated_top_mechanisms = baseline_negated_mechanisms_in_text(baseline_text, top_hypothesis)

    if boundary is None or (boundary.graph_path is None):
        signals.append("missing_graph_context")
        score += 10.0
    if "top_root_diff" in boundary_categories:
        signals.append("root_candidate_mismatch")
        score += 3.0
    if any(item.startswith("top_layer_diff:") for item in boundary_categories):
        signals.append("root_layer_mismatch")
        score += 4.0
    if any(item.startswith("top_root_equiv:") for item in boundary_categories):
        blockers.append("root_boundary_equivalent")
        score -= 2.0
    if "low_baseline_support" in boundary_categories or baseline_support < 0.78:
        signals.append("low_baseline_support")
        score += 2.0
    if baseline_risks:
        signals.append("verifier_baseline_risk")
        score += min(3.0, 0.9 * len(baseline_risks))
    if negated_top_mechanisms:
        signals.append("top_hypothesis_excluded_by_baseline")
        blockers.append("top_hypothesis_negated_by_baseline")
        score -= 3.0

    if excluded_raw:
        signals.append("raw_mechanism_excluded_by_baseline")
    if raw_root_changing:
        signals.append("raw_boundary_mechanism_gap")
        score += min(5.0, 1.8 * len(raw_root_changing))
    elif raw_soft_only:
        signals.append("raw_soft_mechanism_gap")
        score += min(1.2, 0.4 * len(set(raw_uncovered) & SOFT_RAW_MECHANISMS))
    if raw is not None and any(
        category.startswith("nonempty_raw_not_referenced:") for category in raw.categories
    ):
        signals.append("nonempty_raw_not_referenced")
        score += 1.0
    if raw is not None and any(
        category.startswith("raw_family_modality_missing:") for category in raw.categories
    ):
        signals.append("raw_modality_missing")
        score += 1.5

    if any(item.startswith("baseline_layer_diff:") for item in analogue_categories):
        signals.append("validation_layer_gap")
        score += 3.0
    if "analogue_mechanism_gap" in analogue_categories:
        signals.append("validation_mechanism_gap")
        score += 2.0
    if "ambiguous_public_analogues" in analogue_categories:
        signals.append("ambiguous_public_analogues")
        score += 1.4
    if (
        "low_similarity_public_analogue" in analogue_categories
        or "no_public_validation_analogue" in analogue_categories
    ):
        signals.append("weak_public_analogue")
        score += 1.0

    synthetic_trace = not bool(REAL_TRACE_ID_RE.fullmatch(trace_id.strip()))
    direct_trace_gap = _direct_trace_mechanism_gap(raw)
    if synthetic_trace:
        signals.append("synthetic_trace_id")
        if direct_trace_gap:
            signals.append("direct_trace_mechanism_gap")
            score += 0.7
            blockers.append("do_not_submit_trace_only")
        else:
            blockers.append("trace_only_repair_without_direct_root_gap")
            score -= 2.0

    if _case_type(raw, analogue, boundary) == "HSF":
        hsf_signal = (
            "low_baseline_support" in signals
            or "root_layer_mismatch" in signals
            or "validation_layer_gap" in signals
            or "raw_boundary_mechanism_gap" in signals
        )
        if hsf_signal:
            signals.append("hsf_frontier")
            score += 1.5

    if feedback is not None:
        best_delta = feedback.best_delta
        matching_negative = _matching_negative_probe(feedback, raw_root_changing)
        if feedback.positive_count:
            signals.append("positive_public_probe")
            score += 8.0
        if feedback.records and best_delta == 0:
            blockers.append("current_best_probe_anchor")
            score -= 4.0
        if _known_negative(feedback):
            if matching_negative is not None or not raw_root_changing:
                blockers.append("known_negative_probe")
                if raw_root_changing:
                    score -= 3.0
                else:
                    score -= 8.0 + min(4.0, float(feedback.negative_count))
            else:
                blockers.append("case_negative_probe_history")
                signals.append("untried_root_mechanism_after_negative")
                score += 1.0
                score -= min(2.0, 0.4 * float(feedback.negative_count))
        elif feedback.negative_count:
            blockers.append("mixed_negative_probe_history")
            score -= min(3.0, float(feedback.negative_count))
        if best_delta is not None and best_delta < -1.0:
            blockers.append("large_negative_probe_delta")
            score -= 1.5

    tomography_estimate, tomography_observations = _tomography_values(tomography)
    if tomography_estimate is not None:
        if tomography_estimate > 0.05:
            signals.append("positive_tomography_variant")
            score += 10.0
        elif tomography_estimate < -0.05:
            blockers.append("negative_tomography_variant")
            score -= 4.0

    if baseline_support >= 0.9 and not raw_root_changing and not _has_boundary_signal(signals):
        blockers.append("stable_current_best")
        score -= 2.5

    score = round(max(0.0, score), 3)
    bucket = _bucket(signals, blockers, score)
    return FrontierCase(
        case_id=case_id,
        case_suffix=case_suffix(case_id),
        case_type=_case_type(raw, analogue, boundary),
        frontier_score=score,
        bucket=bucket,
        action=_action(bucket, signals, blockers),
        signals=_unique(signals),
        blockers=_unique(blockers),
        graph_path=_graph_path(raw, analogue, boundary),
        baseline_support=round(baseline_support, 4),
        baseline_risks=baseline_risks,
        boundary_score=round(boundary.opportunity_score if boundary is not None else 0.0, 4),
        boundary_categories=boundary_categories,
        analogue_score=round(analogue.priority if analogue is not None else 0.0, 4),
        analogue_categories=analogue_categories,
        analogue_top_case=analogue.matches[0].case_id
        if analogue is not None and analogue.matches
        else "",
        analogue_similarity=analogue.matches[0].similarity
        if analogue is not None and analogue.matches
        else None,
        raw_score=round(raw.priority if raw is not None else 0.0, 4),
        raw_uncovered_mechanisms=raw_uncovered,
        raw_categories=list(raw.categories if raw is not None else []),
        probe_count=len(feedback.records) if feedback is not None else 0,
        negative_probe_count=feedback.negative_count if feedback is not None else 0,
        best_probe_accuracy=_best_probe_accuracy_for_case(feedback),
        best_probe_delta=feedback.best_delta if feedback is not None else None,
        tomography_best_estimate=tomography_estimate,
        tomography_observations=tomography_observations,
        synthetic_trace_id=synthetic_trace,
        top_hypothesis=top_hypothesis,
        baseline_preview=clip_text(baseline_text, 280),
    )


def _target_case_ids(all_case_ids: Sequence[str] | Any, filters: Sequence[str]) -> list[str]:
    case_ids = [str(item) for item in all_case_ids]
    if not filters:
        return case_ids
    lowered = {item.lower() for item in filters}
    return [
        case_id
        for case_id in case_ids
        if case_id.lower() in lowered or case_suffix(case_id) in lowered
    ]


def _feedback_ledger(leaderboard_path: Path | None, team_name: str) -> ProbeFeedbackLedger | None:
    if leaderboard_path is None or not leaderboard_path.exists():
        return None
    payload = load_json(leaderboard_path)
    if not isinstance(payload, dict):
        return None
    return ProbeFeedbackLedger.from_leaderboard(payload, team_name=team_name)


def _tomography_by_case(
    *,
    leaderboard_path: Path | None,
    baseline_path: Path,
    tomography_path: Path | None,
    results_dir: Path,
    team_name: str,
    reference_agent_name: str | None,
) -> dict[str, dict[str, Any]]:
    payload: dict[str, Any] | None = None
    if tomography_path is not None and tomography_path.exists():
        loaded = load_json(tomography_path)
        if isinstance(loaded, dict):
            payload = loaded
    elif leaderboard_path is not None and leaderboard_path.exists():
        try:
            payload = build_tomography_report(
                leaderboard_path=leaderboard_path,
                reference_result_path=baseline_path,
                results_dir=results_dir,
                team_name=team_name,
                reference_agent_name=reference_agent_name,
            ).to_dict()
        except (OSError, ValueError, KeyError):
            payload = None
    if not isinstance(payload, dict):
        return {}
    output: dict[str, dict[str, Any]] = {}
    for item in payload.get("cases", []):
        if not isinstance(item, dict):
            continue
        case_id = str(item.get("case_id") or "")
        suffix = str(item.get("case_suffix") or "")
        if case_id:
            output[case_id] = item
        if suffix:
            output[suffix] = item
    return output


def _tomography_values(tomography: dict[str, Any] | None) -> tuple[float | None, int]:
    if not tomography:
        return None, 0
    estimate = _as_float(tomography.get("best_estimate"))
    observations = 0
    estimates = tomography.get("estimates")
    if isinstance(estimates, list) and estimates:
        first = estimates[0]
        if isinstance(first, dict):
            raw_count = first.get("observation_count")
            observations = int(raw_count) if isinstance(raw_count, int) else 0
    return estimate, observations


def _known_negative(feedback: CaseProbeFeedback) -> bool:
    return feedback.negative_count > 0 and (feedback.best_delta is None or feedback.best_delta <= 0)


def _matching_negative_probe(
    feedback: CaseProbeFeedback,
    raw_mechanisms: Sequence[str],
) -> object | None:
    return feedback.matching_negative_markers(_mechanism_markers(raw_mechanisms))


def _mechanism_markers(raw_mechanisms: Sequence[str]) -> set[str]:
    return mechanism_markers(raw_mechanisms)


def _best_leaderboard_accuracy(ledger: ProbeFeedbackLedger | None) -> float | None:
    return ledger.reference_accuracy if ledger is not None else None


def _best_probe_accuracy_for_case(feedback: CaseProbeFeedback | None) -> float | None:
    if feedback is None or not feedback.records:
        return None
    return max(record.accuracy for record in feedback.records)


def _case_type(
    raw: RawInventoryCase | None,
    analogue: CaseAnalogue | None,
    boundary: BoundaryDeltaCase | None,
) -> str:
    for value in (
        boundary.case_type if boundary is not None else "",
        analogue.case_type if analogue is not None else "",
        raw.case_type if raw is not None else "",
    ):
        if value:
            return value
    return "unknown"


def _graph_path(
    raw: RawInventoryCase | None,
    analogue: CaseAnalogue | None,
    boundary: BoundaryDeltaCase | None,
) -> str | None:
    for value in (
        boundary.graph_path if boundary is not None else None,
        analogue.graph_path if analogue is not None else None,
        raw.graph_path if raw is not None else None,
    ):
        if value:
            return value
    return None


def _top_hypothesis(analogue: CaseAnalogue | None, boundary: BoundaryDeltaCase | None) -> str:
    if boundary is not None and boundary.graph_top_hypothesis:
        return boundary.graph_top_hypothesis
    if analogue is not None and analogue.top_hypothesis:
        return analogue.top_hypothesis
    return ""


def _direct_trace_mechanism_gap(raw: RawInventoryCase | None) -> bool:
    if raw is None:
        return False
    for item in raw.top_files:
        if item.family != "trace":
            continue
        if set(item.uncovered_mechanisms) & ROOT_CHANGING_RAW_MECHANISMS:
            return True
    return False


def _has_boundary_signal(signals: Sequence[str]) -> bool:
    return bool(
        set(signals)
        & {
            "root_candidate_mismatch",
            "root_layer_mismatch",
            "raw_boundary_mechanism_gap",
            "validation_layer_gap",
            "validation_mechanism_gap",
            "positive_tomography_variant",
        }
    )


def _bucket(signals: Sequence[str], blockers: Sequence[str], score: float) -> str:
    signal_set = set(signals)
    blocker_set = set(blockers)
    if "missing_graph_context" in signal_set:
        return "collect_graph"
    if "positive_tomography_variant" in signal_set:
        return "submit_known_positive_variant"
    if "top_hypothesis_negated_by_baseline" in blocker_set:
        if "raw_boundary_mechanism_gap" in signal_set and score >= 1.0:
            return "raw_mechanism_probe"
        if "synthetic_trace_id" in signal_set:
            return "do_not_trace_repair_only"
        return "do_not_probe"
    if (
        "negative_tomography_variant" in blocker_set
        and "root_layer_mismatch" not in signal_set
        and "validation_layer_gap" not in signal_set
    ):
        if "synthetic_trace_id" in signal_set:
            return "do_not_trace_repair_only"
        if "known_negative_probe" in blocker_set or "current_best_probe_anchor" in blocker_set:
            return "do_not_probe"
    if "known_negative_probe" in blocker_set and "raw_boundary_mechanism_gap" not in signal_set:
        if "synthetic_trace_id" in signal_set:
            return "do_not_trace_repair_only"
        return "do_not_probe"
    if "root_layer_mismatch" in signal_set or "validation_layer_gap" in signal_set:
        return "root_boundary_probe"
    if "raw_boundary_mechanism_gap" in signal_set:
        return "raw_mechanism_probe"
    if "hsf_frontier" in signal_set:
        return "hsf_counterfactual_review"
    if "synthetic_trace_id" in signal_set:
        return "do_not_trace_repair_only"
    if score >= 4.0:
        return "frontier_review"
    return "preserve_current_best"


def _action(bucket: str, signals: Sequence[str], blockers: Sequence[str]) -> str:
    if bucket == "collect_graph":
        return "先补 graph_context/raw artifact，再生成候选；当前没有足够证据判断。"
    if bucket == "submit_known_positive_variant":
        return "已有公开反馈推断为正收益；定位本地结果文件后做最小合并提交。"
    if "top_hypothesis_negated_by_baseline" in blockers:
        return "图谱 top 主因已被 current-best 明确降级或排除；除非新增 raw 机制能独立改变根因边界，否则保持 current-best。"
    if bucket == "do_not_probe":
        return "保持 current-best；已有负反馈且没有新增根因机制证据，不再重复同类 probe。"
    if bucket == "do_not_trace_repair_only":
        return "不要做 trace-id-only 或文本清理提交；只有 trace 改变根因实体/机制时才重开候选。"
    if bucket == "root_boundary_probe":
        return (
            "让 DMA 做 root-boundary 反事实：保留 baseline 关键实体，比较触发点、故障点和放大器。"
        )
    if bucket == "raw_mechanism_probe":
        return "先把 raw 机制转成 ontology/evidence bundle，再生成最小候选；避免只扩写证据。"
    if bucket == "hsf_counterfactual_review":
        return "按 HSF caller/provider/downstream 边界做人审包，优先检查 Sentinel、线程池、变更冷启动。"
    if "stable_current_best" in blockers:
        return "当前 best 证据稳定；等待新增数据源或公开正反馈再动。"
    if "weak_public_analogue" in signals:
        return "先扩充 validation analogue/证据覆盖，再决定是否交给 DMA。"
    return "保持 current-best，继续寻找更强边界信号。"


def _first_float(*values: float | None) -> float:
    for value in values:
        if value is not None:
            return float(value)
    return 0.0


def _as_float(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _unique(values: Sequence[str]) -> list[str]:
    output: list[str] = []
    seen: set[str] = set()
    for value in values:
        if value in seen:
            continue
        seen.add(value)
        output.append(value)
    return output


def _top_counts(values: dict[str, int], *, limit: int = 10) -> dict[str, int]:
    return dict(sorted(values.items(), key=lambda item: (-item[1], item[0]))[:limit])


def _cell(value: str) -> str:
    return clip_text(value.replace("|", "/").replace("\n", " "), 96)
