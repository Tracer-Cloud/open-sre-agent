from __future__ import annotations

from collections import Counter
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from tests.benchmarks.realrca_graph.features import clip_text
from tests.benchmarks.realrca_graph.io import DEFAULT_CURRENT_BEST, load_json, rows_by_case
from tests.benchmarks.realrca_graph.mechanism_terms import ROOT_CHANGING_RAW_MECHANISMS

HARD_FEEDBACK_BLOCKERS = {
    "known_negative_probe",
    "large_negative_probe_delta",
    "negative_tomography_variant",
    "case_negative_probe_history",
}


@dataclass(frozen=True)
class ScoreBoundaryCase:
    """One case ranked by public score feedback and graph-boundary signals."""

    case_id: str
    case_suffix: str
    case_type: str
    priority_score: float
    action: str
    categories: list[str]
    blockers: list[str]
    frontier_bucket: str
    frontier_score: float
    frontier_signals: list[str]
    baseline_support: float | None
    best_probe_accuracy: float | None
    best_probe_delta: float | None
    tomography_best_estimate: float | None
    tomography_observations: int
    zero_delta_variants: int
    negative_variants: int
    direct_negative_variants: int
    positive_variants: int
    outlier_score: float | None
    outlier_categories: list[str]
    raw_score: float
    raw_uncovered_mechanisms: list[str]
    baseline_preview: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class ScoreBoundaryReport:
    """Aggregate report for choosing the next RealRCA improvement experiments."""

    baseline_path: str
    frontier_path: str
    tomography_path: str
    answer_outlier_path: str
    case_count: int
    category_counts: dict[str, int]
    action_counts: dict[str, int]
    cases: list[ScoreBoundaryCase]

    def to_dict(self) -> dict[str, Any]:
        return {
            "baseline_path": self.baseline_path,
            "frontier_path": self.frontier_path,
            "tomography_path": self.tomography_path,
            "answer_outlier_path": self.answer_outlier_path,
            "hidden_test_reference_used": False,
            "case_count": self.case_count,
            "category_counts": dict(self.category_counts),
            "action_counts": dict(self.action_counts),
            "cases": [item.to_dict() for item in self.cases],
        }


def build_score_boundary_report(
    *,
    baseline_path: Path = DEFAULT_CURRENT_BEST,
    frontier_path: Path | None = None,
    tomography_path: Path | None = None,
    answer_outlier_path: Path | None = None,
    case_ids: list[str] | None = None,
) -> ScoreBoundaryReport:
    """Rank cases where a new candidate could still improve the public test score."""

    baseline = rows_by_case(baseline_path, source=baseline_path.stem)
    selected = {item.lower() for item in (case_ids or [])}
    frontier_by_case = _cases_by_id(_load_cases(frontier_path))
    tomography_by_case = _cases_by_id(_load_cases(tomography_path))
    outlier_by_case = _cases_by_id(_load_cases(answer_outlier_path))

    cases: list[ScoreBoundaryCase] = []
    for case_id, answer in baseline.items():
        suffix = _case_suffix(case_id)
        if selected and case_id.lower() not in selected and suffix not in selected:
            continue
        frontier = frontier_by_case.get(case_id)
        tomography = tomography_by_case.get(case_id)
        outlier = outlier_by_case.get(case_id)
        cases.append(
            _score_case(
                case_id=case_id,
                suffix=suffix,
                baseline_preview=clip_text(answer.diagnosis_output, 240),
                frontier=frontier,
                tomography=tomography,
                outlier=outlier,
            )
        )

    cases.sort(
        key=lambda item: (
            -item.priority_score,
            item.action == "avoid",
            item.action == "preserve_current_best",
            item.case_type,
            item.case_id,
        )
    )
    return ScoreBoundaryReport(
        baseline_path=str(baseline_path),
        frontier_path=str(frontier_path or ""),
        tomography_path=str(tomography_path or ""),
        answer_outlier_path=str(answer_outlier_path or ""),
        case_count=len(cases),
        category_counts=dict(Counter(category for item in cases for category in item.categories)),
        action_counts=dict(Counter(item.action for item in cases)),
        cases=cases,
    )


def render_score_boundary_markdown(report: ScoreBoundaryReport, *, limit: int = 60) -> str:
    """Render a compact experiment-planning report."""

    lines = [
        "# RealRCA Score Boundary Report",
        "",
        f"- baseline: `{report.baseline_path}`",
        f"- frontier: `{report.frontier_path}`",
        f"- tomography: `{report.tomography_path}`",
        f"- answer_outliers: `{report.answer_outlier_path}`",
        "- hidden_test_reference_used: `False`",
        f"- cases: `{report.case_count}`",
        f"- action_counts: `{_top_counts(report.action_counts)}`",
        f"- category_counts: `{_top_counts(report.category_counts)}`",
        "",
        "| rank | case | type | priority | action | frontier | tomo | zero/neg/direct | outlier | blockers | categories |",
        "| --- | --- | --- | ---: | --- | --- | ---: | --- | ---: | --- | --- |",
    ]
    for index, item in enumerate(report.cases[:limit], start=1):
        lines.append(
            "| "
            + " | ".join(
                [
                    str(index),
                    f"`{item.case_suffix}`",
                    item.case_type or "-",
                    f"{item.priority_score:.3f}",
                    item.action,
                    item.frontier_bucket or "-",
                    _fmt(item.tomography_best_estimate),
                    f"{item.zero_delta_variants}/{item.negative_variants}/{item.direct_negative_variants}",
                    _fmt(item.outlier_score),
                    ",".join(item.blockers[:3]) or "-",
                    ",".join(item.categories[:4]) or "-",
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
                f"- priority: `{item.priority_score}`; action: `{item.action}`",
                (
                    f"- frontier: bucket=`{item.frontier_bucket}` score=`{item.frontier_score}` "
                    f"signals=`{item.frontier_signals}` blockers=`{item.blockers}`"
                ),
                (
                    f"- tomography: best=`{item.tomography_best_estimate}` observations=`{item.tomography_observations}` "
                    f"zero=`{item.zero_delta_variants}` negative=`{item.negative_variants}` "
                    f"direct_negative=`{item.direct_negative_variants}`"
                ),
                f"- outlier: score=`{item.outlier_score}` categories=`{item.outlier_categories}`",
                f"- baseline_preview: {item.baseline_preview}",
                "",
            ]
        )
    return "\n".join(lines).rstrip() + "\n"


def _score_case(
    *,
    case_id: str,
    suffix: str,
    baseline_preview: str,
    frontier: dict[str, Any] | None,
    tomography: dict[str, Any] | None,
    outlier: dict[str, Any] | None,
) -> ScoreBoundaryCase:
    frontier_bucket = str((frontier or {}).get("bucket") or "")
    frontier_score = _float((frontier or {}).get("frontier_score"))
    frontier_signals = _str_list((frontier or {}).get("signals"))
    frontier_blockers = _str_list((frontier or {}).get("blockers"))
    raw_score = _float((frontier or {}).get("raw_score"))
    raw_uncovered = _str_list((frontier or {}).get("raw_uncovered_mechanisms"))
    root_changing_raw = sorted(set(raw_uncovered) & ROOT_CHANGING_RAW_MECHANISMS)
    estimates = _list((tomography or {}).get("estimates"))
    zero_delta = sum(1 for item in estimates if abs(_float(item.get("estimate"))) < 0.05)
    negative = sum(1 for item in estimates if _float(item.get("estimate")) < -0.05)
    positive = sum(1 for item in estimates if _float(item.get("estimate")) > 0.05)
    direct_negative = sum(
        1
        for item in estimates
        if _float(item.get("estimate")) < -0.05
        and "direct_single_case" in _str_list(item.get("methods"))
    )
    observations = sum(int(item.get("observation_count") or 0) for item in estimates)
    tomography_best = _none_float((tomography or {}).get("best_estimate"))
    outlier_score = _none_float((outlier or {}).get("outlier_score"))
    outlier_categories = _str_list((outlier or {}).get("categories"))
    family_untried_after_negative = (
        zero_delta > 0
        and direct_negative == 0
        and "untried_root_mechanism_after_negative" in frontier_signals
        and bool(root_changing_raw)
    )
    hard_blockers = sorted(set(frontier_blockers) & HARD_FEEDBACK_BLOCKERS)
    if family_untried_after_negative:
        hard_blockers = [
            blocker
            for blocker in hard_blockers
            if blocker in {"known_negative_probe", "negative_tomography_variant"}
        ]
    categories: list[str] = []
    score = 0.0

    if positive:
        categories.append("known_positive_variant")
        score += 8.0 + positive
    if not estimates:
        categories.append("no_public_variant_feedback")
        score += 1.5
    if zero_delta:
        categories.append("zero_delta_uncertain")
        score += min(2.0, zero_delta * 0.65)
    if direct_negative:
        categories.append("direct_single_case_negative")
        score -= min(4.0, direct_negative * 1.25)
    elif negative:
        categories.append("non_direct_negative_feedback")
        score -= min(2.0, negative * 0.35)
    if frontier_bucket in {"root_boundary_probe", "raw_mechanism_probe"}:
        categories.append(f"frontier:{frontier_bucket}")
        score += min(2.2, frontier_score / 2.8)
    if outlier_score and outlier_score > 0.2:
        categories.append("answer_outlier_signal")
        score += min(2.0, outlier_score)
    if root_changing_raw:
        categories.append("root_changing_raw_gap")
    if family_untried_after_negative:
        categories.append("soft_negative_history_untried_mechanism")
        score += 0.8
    if hard_blockers:
        categories.append("hard_public_feedback_blocker")
        score -= 2.0 + min(2.0, 0.4 * len(hard_blockers))
    stable_supported = (frontier or {}).get("baseline_support") == 1.0 and not positive
    if stable_supported:
        categories.append("stable_supported_baseline")
        score -= 0.7
    stable_low_information_gap = _is_stable_low_information_gap(
        stable_supported=stable_supported,
        has_estimates=bool(estimates),
        hard_blockers=hard_blockers,
        frontier_bucket=frontier_bucket,
        frontier_score=frontier_score,
        frontier_signals=frontier_signals,
        outlier_score=outlier_score,
        raw_score=raw_score,
        root_changing_raw=bool(root_changing_raw),
    )
    if stable_low_information_gap:
        categories.append("stable_low_information_gap")
        score -= 0.8

    action = _action(
        score=score,
        positive=positive,
        hard_blockers=hard_blockers,
        direct_negative=direct_negative,
        zero_delta=zero_delta,
        has_estimates=bool(estimates),
        frontier_bucket=frontier_bucket,
        outlier_score=outlier_score,
        stable_low_information_gap=stable_low_information_gap,
    )
    priority = round(max(0.0, score), 3)
    if not categories:
        categories.append("no_boundary_signal")
    return ScoreBoundaryCase(
        case_id=case_id,
        case_suffix=suffix,
        case_type=str((frontier or outlier or {}).get("case_type") or ""),
        priority_score=priority,
        action=action,
        categories=categories,
        blockers=frontier_blockers,
        frontier_bucket=frontier_bucket,
        frontier_score=frontier_score,
        frontier_signals=frontier_signals,
        baseline_support=_none_float((frontier or {}).get("baseline_support")),
        best_probe_accuracy=_none_float((frontier or {}).get("best_probe_accuracy")),
        best_probe_delta=_none_float((frontier or {}).get("best_probe_delta")),
        tomography_best_estimate=tomography_best,
        tomography_observations=observations,
        zero_delta_variants=zero_delta,
        negative_variants=negative,
        direct_negative_variants=direct_negative,
        positive_variants=positive,
        outlier_score=outlier_score,
        outlier_categories=outlier_categories,
        raw_score=raw_score,
        raw_uncovered_mechanisms=raw_uncovered,
        baseline_preview=baseline_preview,
    )


def _is_stable_low_information_gap(
    *,
    stable_supported: bool,
    has_estimates: bool,
    hard_blockers: list[str],
    frontier_bucket: str,
    frontier_score: float,
    frontier_signals: list[str],
    outlier_score: float | None,
    raw_score: float,
    root_changing_raw: bool,
) -> bool:
    if not stable_supported or has_estimates or hard_blockers:
        return False
    if frontier_bucket != "raw_mechanism_probe" or frontier_score > 2.0:
        return False
    if root_changing_raw and (raw_score >= 5.0 or (outlier_score or 0.0) >= 0.5):
        return False
    if (outlier_score or 0.0) > 0.75:
        return False
    high_boundary_signals = {
        "root_candidate_mismatch",
        "root_layer_mismatch",
        "validation_mechanism_gap",
        "verifier_baseline_risk",
    }
    return not bool(set(frontier_signals) & high_boundary_signals)


def _action(
    *,
    score: float,
    positive: int,
    hard_blockers: list[str],
    direct_negative: int,
    zero_delta: int,
    has_estimates: bool,
    frontier_bucket: str,
    outlier_score: float | None,
    stable_low_information_gap: bool = False,
) -> str:
    if positive:
        return "submit_known_positive_variant"
    if stable_low_information_gap:
        return "preserve_current_best"
    if hard_blockers and direct_negative:
        return "avoid"
    if score >= 2.0 and (not hard_blockers or zero_delta):
        return "generate_boundary_challenger"
    if not has_estimates and (
        frontier_bucket in {"root_boundary_probe", "raw_mechanism_probe"}
        or (outlier_score or 0) > 0
    ):
        return "generate_candidate"
    if hard_blockers or direct_negative:
        return "avoid"
    return "preserve_current_best"


def _load_cases(path: Path | None) -> list[dict[str, Any]]:
    if path is None or not path.exists():
        return []
    payload = load_json(path)
    cases = payload.get("cases", []) if isinstance(payload, dict) else []
    return [item for item in cases if isinstance(item, dict)]


def _cases_by_id(cases: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    output: dict[str, dict[str, Any]] = {}
    for item in cases:
        case_id = item.get("case_id")
        if isinstance(case_id, str):
            output[case_id] = item
    return output


def _case_suffix(case_id: str) -> str:
    return case_id.rsplit("-", 1)[-1][-4:].lower()


def _str_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item) for item in value if str(item)]


def _list(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, dict)]


def _float(value: Any) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _none_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _fmt(value: float | None) -> str:
    if value is None:
        return "-"
    return f"{value:.2f}"


def _top_counts(counts: dict[str, int], *, limit: int = 8) -> str:
    return ", ".join(f"{key}={value}" for key, value in Counter(counts).most_common(limit))
