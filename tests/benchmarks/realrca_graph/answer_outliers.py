from __future__ import annotations

from collections import Counter
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from tests.benchmarks.realrca_graph.features import clip_text, keyword_features
from tests.benchmarks.realrca_graph.io import DEFAULT_CURRENT_BEST, load_json, rows_by_case
from tests.benchmarks.realrca_graph.models import CandidateAnswer

HARD_BLOCKERS = {
    "known_negative_probe",
    "large_negative_probe_delta",
    "negative_tomography_variant",
    "top_hypothesis_negated_by_baseline",
}


@dataclass(frozen=True)
class AnswerOutlierCase:
    """One case whose current answer differs from graph-analogue answer patterns."""

    case_id: str
    case_suffix: str
    case_type: str
    outlier_score: float
    answer_mechanisms: list[str]
    internal_answer_overlap: float | None
    public_mechanism_overlap: float | None
    top_internal_analogue: str
    top_internal_similarity: float | None
    top_public_analogue: str
    top_public_similarity: float | None
    frontier_bucket: str
    frontier_score: float
    frontier_signals: list[str]
    frontier_blockers: list[str]
    categories: list[str]
    baseline_preview: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class AnswerOutlierReport:
    """Aggregate report for graph-analogue answer-boundary outliers."""

    baseline_path: str
    internal_analogue_path: str
    public_analogue_path: str
    frontier_path: str
    case_count: int
    category_counts: dict[str, int]
    cases: list[AnswerOutlierCase]

    def to_dict(self) -> dict[str, Any]:
        return {
            "baseline_path": self.baseline_path,
            "internal_analogue_path": self.internal_analogue_path,
            "public_analogue_path": self.public_analogue_path,
            "frontier_path": self.frontier_path,
            "hidden_test_reference_used": False,
            "case_count": self.case_count,
            "category_counts": dict(self.category_counts),
            "cases": [item.to_dict() for item in self.cases],
        }


def build_answer_outlier_report(
    *,
    baseline_path: Path = DEFAULT_CURRENT_BEST,
    internal_analogue_path: Path | None = None,
    public_analogue_path: Path | None = None,
    frontier_path: Path | None = None,
    case_ids: list[str] | None = None,
) -> AnswerOutlierReport:
    """Rank likely root-boundary outliers using graph analogues and visible answers."""

    baseline = rows_by_case(baseline_path, source=baseline_path.stem)
    selected = {item.lower() for item in (case_ids or [])}
    internal = _cases_by_id(_load_cases(internal_analogue_path))
    public = _cases_by_id(_load_cases(public_analogue_path))
    frontier = _cases_by_id(_load_cases(frontier_path))
    cases: list[AnswerOutlierCase] = []
    for case_id, answer in baseline.items():
        if selected and case_id.lower() not in selected and _case_suffix(case_id) not in selected:
            continue
        item = _outlier_case(
            answer=answer,
            internal=internal.get(case_id),
            public=public.get(case_id),
            frontier=frontier.get(case_id),
            baseline_by_case=baseline,
        )
        cases.append(item)
    cases.sort(key=lambda item: (-item.outlier_score, item.case_type, item.case_id))
    return AnswerOutlierReport(
        baseline_path=str(baseline_path),
        internal_analogue_path=str(internal_analogue_path or ""),
        public_analogue_path=str(public_analogue_path or ""),
        frontier_path=str(frontier_path or ""),
        case_count=len(cases),
        category_counts=dict(Counter(category for item in cases for category in item.categories)),
        cases=cases,
    )


def render_answer_outlier_markdown(report: AnswerOutlierReport, *, limit: int = 60) -> str:
    """Render a compact answer-boundary outlier report."""

    lines = [
        "# RealRCA Answer Boundary Outliers",
        "",
        f"- baseline: `{report.baseline_path}`",
        f"- internal_analogues: `{report.internal_analogue_path}`",
        f"- public_analogues: `{report.public_analogue_path}`",
        f"- frontier: `{report.frontier_path}`",
        "- hidden_test_reference_used: `False`",
        f"- cases: `{report.case_count}`",
        f"- top_categories: `{_top_counts(report.category_counts)}`",
        "",
        "## Ranked Cases",
        "",
        "| rank | case | type | score | answer_mechanisms | internal_overlap | public_overlap | frontier | blockers | categories |",
        "| --- | --- | --- | ---: | --- | ---: | ---: | --- | --- | --- |",
    ]
    for index, item in enumerate(report.cases[:limit], start=1):
        lines.append(
            "| "
            + " | ".join(
                [
                    str(index),
                    f"`{item.case_suffix}`",
                    item.case_type or "-",
                    f"{item.outlier_score:.3f}",
                    ",".join(item.answer_mechanisms[:5]) or "-",
                    _fmt(item.internal_answer_overlap),
                    _fmt(item.public_mechanism_overlap),
                    item.frontier_bucket or "-",
                    ",".join(item.frontier_blockers[:3]) or "-",
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
                f"- score: `{item.outlier_score}`; categories: `{item.categories}`",
                f"- answer_mechanisms: `{item.answer_mechanisms}`",
                (
                    f"- internal: analogue=`{item.top_internal_analogue}` "
                    f"similarity=`{item.top_internal_similarity}` "
                    f"answer_overlap=`{item.internal_answer_overlap}`"
                ),
                (
                    f"- public: analogue=`{item.top_public_analogue}` "
                    f"similarity=`{item.top_public_similarity}` "
                    f"mechanism_overlap=`{item.public_mechanism_overlap}`"
                ),
                (
                    f"- frontier: bucket=`{item.frontier_bucket}` score=`{item.frontier_score}` "
                    f"signals=`{item.frontier_signals}` blockers=`{item.frontier_blockers}`"
                ),
                f"- baseline_preview: {item.baseline_preview}",
                "",
            ]
        )
    return "\n".join(lines).rstrip() + "\n"


def _outlier_case(
    *,
    answer: CandidateAnswer,
    internal: dict[str, Any] | None,
    public: dict[str, Any] | None,
    frontier: dict[str, Any] | None,
    baseline_by_case: dict[str, CandidateAnswer],
) -> AnswerOutlierCase:
    answer_mechanisms = sorted(keyword_features(answer.diagnosis_output))
    internal_overlap = _internal_answer_overlap(answer, internal, baseline_by_case)
    public_overlap = _public_mechanism_overlap(answer_mechanisms, public)
    internal_top = _top_match(internal)
    public_top = _top_match(public)
    frontier_bucket = str((frontier or {}).get("bucket") or "")
    frontier_score = _float((frontier or {}).get("frontier_score"))
    frontier_signals = _str_list((frontier or {}).get("signals"))
    frontier_blockers = _str_list((frontier or {}).get("blockers"))
    categories: list[str] = []
    score = 0.0
    if (
        internal_top
        and internal_top.get("similarity", 0) >= 0.65
        and internal_overlap is not None
        and internal_overlap < 0.45
    ):
        categories.append("internal_answer_mechanism_outlier")
        score += 3.0 * (1.0 - internal_overlap)
    if (
        public_top
        and public_top.get("similarity", 0) >= 0.70
        and public_overlap is not None
        and public_overlap < 0.45
    ):
        categories.append("public_graph_mechanism_outlier")
        score += 2.2 * (1.0 - public_overlap)
    if frontier_bucket in {"root_boundary_probe", "raw_mechanism_probe"}:
        categories.append(f"frontier:{frontier_bucket}")
        score += min(2.0, frontier_score / 3.0)
    hard_blockers = sorted(set(frontier_blockers) & HARD_BLOCKERS)
    if hard_blockers:
        categories.append("hard_negative_feedback")
        score -= 2.5 + min(1.5, 0.3 * len(hard_blockers))
    if not categories:
        categories.append("no_outlier_signal")
    score = round(max(0.0, score), 3)
    return AnswerOutlierCase(
        case_id=answer.case_id,
        case_suffix=_case_suffix(answer.case_id),
        case_type=str((internal or public or frontier or {}).get("case_type") or ""),
        outlier_score=score,
        answer_mechanisms=answer_mechanisms,
        internal_answer_overlap=internal_overlap,
        public_mechanism_overlap=public_overlap,
        top_internal_analogue=_match_label(internal_top),
        top_internal_similarity=_match_similarity(internal_top),
        top_public_analogue=_match_label(public_top),
        top_public_similarity=_match_similarity(public_top),
        frontier_bucket=frontier_bucket,
        frontier_score=frontier_score,
        frontier_signals=frontier_signals,
        frontier_blockers=frontier_blockers,
        categories=categories,
        baseline_preview=clip_text(answer.diagnosis_output, 260),
    )


def _internal_answer_overlap(
    answer: CandidateAnswer,
    analogue_case: dict[str, Any] | None,
    baseline_by_case: dict[str, CandidateAnswer],
) -> float | None:
    matches = _matches(analogue_case)
    overlaps: list[float] = []
    answer_mechanisms = set(keyword_features(answer.diagnosis_output))
    if not answer_mechanisms:
        return None
    for match in matches[:5]:
        other = baseline_by_case.get(str(match.get("case_id") or ""))
        if other is None:
            continue
        other_mechanisms = keyword_features(other.diagnosis_output)
        overlaps.append(len(answer_mechanisms & other_mechanisms) / len(answer_mechanisms))
    if not overlaps:
        return None
    return round(sum(overlaps) / len(overlaps), 4)


def _public_mechanism_overlap(
    answer_mechanisms: list[str],
    analogue_case: dict[str, Any] | None,
) -> float | None:
    if not answer_mechanisms:
        return None
    matches = _matches(analogue_case)
    if not matches:
        return None
    public_mechanisms: set[str] = set()
    for match in matches[:3]:
        public_mechanisms.update(_str_list(match.get("matched_mechanisms")))
    if not public_mechanisms:
        return None
    return round(len(set(answer_mechanisms) & public_mechanisms) / len(set(answer_mechanisms)), 4)


def _load_cases(path: Path | None) -> list[dict[str, Any]]:
    if path is None or not path.exists():
        return []
    payload = load_json(path)
    if not isinstance(payload, dict):
        return []
    return [item for item in payload.get("cases", []) if isinstance(item, dict)]


def _cases_by_id(cases: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    output: dict[str, dict[str, Any]] = {}
    for item in cases:
        case_id = str(item.get("case_id") or "")
        suffix = str(item.get("case_suffix") or "")
        if case_id:
            output[case_id] = item
        if suffix:
            output[suffix] = item
    return output


def _matches(case: dict[str, Any] | None) -> list[dict[str, Any]]:
    if not isinstance(case, dict):
        return []
    return [item for item in case.get("matches", []) if isinstance(item, dict)]


def _top_match(case: dict[str, Any] | None) -> dict[str, Any] | None:
    matches = _matches(case)
    return matches[0] if matches else None


def _match_label(match: dict[str, Any] | None) -> str:
    if not match:
        return ""
    split = str(match.get("split") or "")
    suffix = _case_suffix(str(match.get("case_id") or ""))
    return f"{split}:{suffix}" if split else suffix


def _match_similarity(match: dict[str, Any] | None) -> float | None:
    if not match:
        return None
    return _float(match.get("similarity"))


def _str_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item) for item in value if str(item)]


def _float(value: Any) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _fmt(value: float | None) -> str:
    return "-" if value is None else f"{value:.4f}"


def _case_suffix(case_id: str) -> str:
    return case_id.rsplit("-", 1)[-1][-4:]


def _top_counts(counts: dict[str, int], *, limit: int = 8) -> str:
    return ", ".join(f"{name}={count}" for name, count in Counter(counts).most_common(limit))
