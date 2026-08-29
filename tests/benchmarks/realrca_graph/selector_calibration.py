from __future__ import annotations

from collections import Counter
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from tests.benchmarks.realrca_graph.bundle_cache import build_evidence_bundle_cached
from tests.benchmarks.realrca_graph.features import clip_text
from tests.benchmarks.realrca_graph.io import (
    DATASET_DIR,
    DEFAULT_GRAPH_ROOT,
    graph_context_path,
    load_cases,
    load_json,
    rows_by_case,
)
from tests.benchmarks.realrca_graph.llm_verifier import has_hard_risk
from tests.benchmarks.realrca_graph.models import CandidateAnswer, CandidateScore, EvidenceBundle
from tests.benchmarks.realrca_graph.validation import ValidationCaseScore, score_validation_answer
from tests.benchmarks.realrca_graph.verifier import score_candidate

GRAPH_HIGH_SUPPORT = 0.58
GRAPH_LOW_SUPPORT = 0.45
ANSWER_LOW_SCORE = 0.55
ANSWER_HIGH_SCORE = 0.75
CRITICAL_LOW_COVERAGE = 0.6
ANSWER_CONTRACT_MIN = 0.62


@dataclass(frozen=True)
class SelectorCalibrationCandidate:
    """One answer scored by graph verifier and public validation truth."""

    case_id: str
    source: str
    graph_support: float
    answer_contract_score: float
    loose_score: float
    critical_coverage: float
    item_coverage: float
    token_recall: float
    modality_count: int
    novelty: float
    baseline_retention: float
    best_hypothesis_label: str
    risk_flags: list[str]
    contract_flags: list[str]
    missing_critical_items: list[str]
    categories: list[str]
    trace_id: str
    diagnosis_preview: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class SelectorCalibrationCase:
    """Public-validation selector calibration for one case."""

    case_id: str
    case_type: str
    graph_path: str | None
    candidate_count: int
    best_by_graph_source: str
    best_by_validation_source: str
    candidates: list[SelectorCalibrationCandidate]

    def to_dict(self) -> dict[str, Any]:
        return {
            "case_id": self.case_id,
            "case_type": self.case_type,
            "graph_path": self.graph_path,
            "candidate_count": self.candidate_count,
            "best_by_graph_source": self.best_by_graph_source,
            "best_by_validation_source": self.best_by_validation_source,
            "candidates": [candidate.to_dict() for candidate in self.candidates],
        }


@dataclass(frozen=True)
class SelectorSourceSummary:
    """Aggregate calibration for one result source."""

    source: str
    case_count: int
    avg_graph_support: float
    avg_loose_score: float
    avg_critical_coverage: float
    hard_risk_count: int
    category_counts: dict[str, int]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class SelectorCalibrationReport:
    """Public truth calibration for graph selector and deterministic verifier."""

    split: str
    graph_roots: list[str]
    result_paths: list[str]
    baseline_path: str | None
    case_count: int
    candidate_count: int
    missing_graph_count: int
    category_counts: dict[str, int]
    source_summaries: list[SelectorSourceSummary]
    cases: list[SelectorCalibrationCase]

    def to_dict(self) -> dict[str, Any]:
        return {
            "split": self.split,
            "graph_roots": list(self.graph_roots),
            "result_paths": list(self.result_paths),
            "baseline_path": self.baseline_path,
            "public_validation_truth_used": True,
            "hidden_test_reference_used": False,
            "case_count": self.case_count,
            "candidate_count": self.candidate_count,
            "missing_graph_count": self.missing_graph_count,
            "category_counts": dict(self.category_counts),
            "source_summaries": [summary.to_dict() for summary in self.source_summaries],
            "cases": [case.to_dict() for case in self.cases],
        }


def build_selector_calibration_report(
    *,
    result_paths: list[Path],
    graph_roots: list[Path],
    split: str = "validation",
    dataset_dir: Path = DATASET_DIR,
    baseline_path: Path | None = None,
    evidence_limit: int = 32,
    hypothesis_limit: int = 10,
    support_limit: int = 4,
) -> SelectorCalibrationReport:
    """Calibrate graph/verifier scores against public validation truth."""

    roots = graph_roots or [DEFAULT_GRAPH_ROOT]
    truths = _truth_rows(dataset_dir)
    metas = _case_meta_by_id(split, dataset_dir)
    result_rows = {
        path: rows_by_case(path, source=path.stem) for path in result_paths if path.exists()
    }
    baseline_rows = rows_by_case(baseline_path, source=baseline_path.stem) if baseline_path else {}
    cases: list[SelectorCalibrationCase] = []
    category_counts: Counter[str] = Counter()
    source_items: dict[str, list[SelectorCalibrationCandidate]] = {}
    missing_graph_count = 0

    for case_id in _ordered_case_ids(split, dataset_dir, truths):
        truth = truths.get(case_id)
        if truth is None:
            continue
        meta = metas.get(case_id, {})
        case_type = str(meta.get("type") or "")
        graph_path = _find_graph_context_path(roots, split, case_id)
        if graph_path is None:
            missing_graph_count += 1
            cases.append(
                SelectorCalibrationCase(
                    case_id=case_id,
                    case_type=case_type,
                    graph_path=None,
                    candidate_count=0,
                    best_by_graph_source="",
                    best_by_validation_source="",
                    candidates=[],
                )
            )
            continue

        bundle = build_evidence_bundle_cached(
            graph_path,
            evidence_limit=evidence_limit,
            hypothesis_limit=hypothesis_limit,
            support_limit=support_limit,
        )
        candidates = [
            _score_answer(
                answer=answer,
                baseline=baseline_rows.get(case_id) or answer,
                truth=truth,
                case_type=case_type or bundle.case_type,
                bundle=bundle,
            )
            for rows in result_rows.values()
            for answer in [rows.get(case_id)]
            if answer is not None
        ]
        candidates.sort(
            key=lambda item: (
                "graph_high_answer_low" not in item.categories,
                -item.graph_support,
                item.loose_score,
                item.source,
            )
        )
        for candidate in candidates:
            category_counts.update(candidate.categories)
            source_items.setdefault(candidate.source, []).append(candidate)
        cases.append(
            SelectorCalibrationCase(
                case_id=case_id,
                case_type=case_type or bundle.case_type,
                graph_path=str(graph_path),
                candidate_count=len(candidates),
                best_by_graph_source=_best_source_by(candidates, "graph_support"),
                best_by_validation_source=_best_source_by(candidates, "loose_score"),
                candidates=candidates,
            )
        )

    cases.sort(
        key=lambda item: (
            _case_priority(item),
            item.case_id,
        )
    )
    return SelectorCalibrationReport(
        split=split,
        graph_roots=[str(root) for root in roots],
        result_paths=[str(path) for path in result_rows],
        baseline_path=str(baseline_path) if baseline_path else None,
        case_count=len(cases),
        candidate_count=sum(case.candidate_count for case in cases),
        missing_graph_count=missing_graph_count,
        category_counts=dict(sorted(category_counts.items())),
        source_summaries=_source_summaries(source_items),
        cases=cases,
    )


def render_selector_calibration_markdown(
    report: SelectorCalibrationReport,
    *,
    limit: int = 60,
) -> str:
    """Render a compact selector calibration report for Yuque or local review."""

    lines = [
        "# RealRCA Selector Calibration",
        "",
        f"- split: `{report.split}`",
        f"- cases: `{report.case_count}`",
        f"- candidates: `{report.candidate_count}`",
        f"- missing_graphs: `{report.missing_graph_count}`",
        "- public_validation_truth_used: `True`",
        "- hidden_test_reference_used: `False`",
        f"- category_counts: `{report.category_counts}`",
        "",
        "## Source Summary",
        "",
        "| source | cases | graph | loose | critical | hard_risk | top categories |",
        "| --- | ---: | ---: | ---: | ---: | ---: | --- |",
    ]
    for summary in report.source_summaries:
        lines.append(
            "| "
            + " | ".join(
                [
                    f"`{summary.source}`",
                    str(summary.case_count),
                    f"{summary.avg_graph_support:.4f}",
                    f"{summary.avg_loose_score:.4f}",
                    f"{summary.avg_critical_coverage:.4f}",
                    str(summary.hard_risk_count),
                    _markdown_cell(_top_categories(summary.category_counts)),
                ]
            )
            + " |"
        )
    lines.extend(
        [
            "",
            "## Highest Mismatches",
            "",
            "| case | type | source | graph | loose | critical | categories | missing critical | hypothesis |",
            "| --- | --- | --- | ---: | ---: | ---: | --- | --- | --- |",
        ]
    )
    for case in report.cases[:limit]:
        candidate = case.candidates[0] if case.candidates else None
        if candidate is None:
            lines.append(
                "| "
                + " | ".join(
                    [
                        f"`{case.case_id[-4:]}`",
                        case.case_type or "-",
                        "-",
                        "-",
                        "-",
                        "-",
                        "missing_graph",
                        "-",
                        "-",
                    ]
                )
                + " |"
            )
            continue
        lines.append(
            "| "
            + " | ".join(
                [
                    f"`{case.case_id[-4:]}`",
                    case.case_type or "-",
                    f"`{candidate.source}`",
                    f"{candidate.graph_support:.4f}",
                    f"{candidate.loose_score:.4f}",
                    f"{candidate.critical_coverage:.4f}",
                    _markdown_cell(", ".join(candidate.categories)),
                    _markdown_cell(", ".join(candidate.missing_critical_items[:3]) or "-"),
                    _markdown_cell(candidate.best_hypothesis_label or "-"),
                ]
            )
            + " |"
        )
    return "\n".join(lines) + "\n"


def _score_answer(
    *,
    answer: CandidateAnswer,
    baseline: CandidateAnswer,
    truth: dict[str, Any],
    case_type: str,
    bundle: EvidenceBundle,
) -> SelectorCalibrationCandidate:
    graph_score = score_candidate(answer, baseline, bundle)
    validation_score = score_validation_answer(answer, truth, case_type=case_type)
    categories = _candidate_categories(graph_score, validation_score)
    return SelectorCalibrationCandidate(
        case_id=answer.case_id,
        source=answer.source,
        graph_support=graph_score.graph_support,
        answer_contract_score=graph_score.answer_contract_score,
        loose_score=validation_score.loose_score,
        critical_coverage=validation_score.critical_coverage,
        item_coverage=validation_score.item_coverage,
        token_recall=validation_score.token_recall,
        modality_count=graph_score.modality_count,
        novelty=graph_score.novelty,
        baseline_retention=graph_score.baseline_retention,
        best_hypothesis_label=graph_score.best_hypothesis_label,
        risk_flags=list(graph_score.risk_flags),
        contract_flags=list(graph_score.contract_flags),
        missing_critical_items=list(validation_score.missing_critical_items),
        categories=categories,
        trace_id=answer.trace_id,
        diagnosis_preview=clip_text(answer.diagnosis_output, 420),
    )


def _candidate_categories(
    graph_score: CandidateScore,
    validation_score: ValidationCaseScore,
) -> list[str]:
    categories: list[str] = []
    if (
        graph_score.graph_support >= GRAPH_HIGH_SUPPORT
        and validation_score.loose_score < ANSWER_LOW_SCORE
    ):
        categories.append("graph_high_answer_low")
    if (
        graph_score.graph_support >= GRAPH_HIGH_SUPPORT
        and validation_score.critical_coverage < CRITICAL_LOW_COVERAGE
    ):
        categories.append("graph_strong_but_critical_missing")
    if (
        validation_score.loose_score >= ANSWER_HIGH_SCORE
        and graph_score.graph_support < GRAPH_LOW_SUPPORT
    ):
        categories.append("answer_high_graph_low")
    if has_hard_risk(graph_score) and validation_score.loose_score < ANSWER_HIGH_SCORE:
        categories.append("hard_risk_low_answer")
    if (
        graph_score.answer_contract_score < ANSWER_CONTRACT_MIN
        and validation_score.loose_score < ANSWER_HIGH_SCORE
    ):
        categories.append("contract_gap_low_answer")
    if "no_hypothesis_overlap" in graph_score.risk_flags:
        categories.append("no_hypothesis_overlap")
    if not categories:
        if (
            graph_score.graph_support >= GRAPH_HIGH_SUPPORT
            and validation_score.loose_score >= ANSWER_HIGH_SCORE
            and validation_score.critical_coverage >= CRITICAL_LOW_COVERAGE
        ):
            categories.append("selector_aligned")
        else:
            categories.append("neutral")
    return categories


def _truth_rows(dataset_dir: Path) -> dict[str, dict[str, Any]]:
    return {
        row["case_id"]: row
        for row in load_json(dataset_dir / "validation_ground_truth.json")
        if isinstance(row, dict) and isinstance(row.get("case_id"), str)
    }


def _case_meta_by_id(split: str, dataset_dir: Path) -> dict[str, dict[str, Any]]:
    return {
        str(row["case_id"]): row
        for row in load_cases(split, dataset_dir)
        if isinstance(row, dict) and isinstance(row.get("case_id"), str)
    }


def _ordered_case_ids(
    split: str, dataset_dir: Path, truths: dict[str, dict[str, Any]]
) -> list[str]:
    ordered = [
        str(row["case_id"])
        for row in load_cases(split, dataset_dir)
        if isinstance(row, dict) and isinstance(row.get("case_id"), str)
    ]
    return ordered or sorted(truths)


def _find_graph_context_path(graph_roots: list[Path], split: str, case_id: str) -> Path | None:
    for root in graph_roots:
        path = graph_context_path(root, split, case_id)
        if path.exists():
            return path
    return None


def _best_source_by(candidates: list[SelectorCalibrationCandidate], field_name: str) -> str:
    if not candidates:
        return ""
    best = max(candidates, key=lambda item: (float(getattr(item, field_name)), item.source))
    return best.source


def _case_priority(case: SelectorCalibrationCase) -> tuple[int, float, float]:
    if not case.candidates:
        return (3, 0.0, 0.0)
    top = case.candidates[0]
    if "graph_high_answer_low" in top.categories:
        return (0, -top.graph_support, top.loose_score)
    if "graph_strong_but_critical_missing" in top.categories:
        return (1, -top.graph_support, top.critical_coverage)
    return (2, -top.graph_support, top.loose_score)


def _source_summaries(
    source_items: dict[str, list[SelectorCalibrationCandidate]],
) -> list[SelectorSourceSummary]:
    summaries: list[SelectorSourceSummary] = []
    for source, items in source_items.items():
        category_counts = Counter(category for item in items for category in item.categories)
        summaries.append(
            SelectorSourceSummary(
                source=source,
                case_count=len(items),
                avg_graph_support=round(
                    sum(item.graph_support for item in items) / max(1, len(items)),
                    4,
                ),
                avg_loose_score=round(
                    sum(item.loose_score for item in items) / max(1, len(items)),
                    4,
                ),
                avg_critical_coverage=round(
                    sum(item.critical_coverage for item in items) / max(1, len(items)),
                    4,
                ),
                hard_risk_count=sum(
                    1 for item in items if "hard_risk_low_answer" in item.categories
                ),
                category_counts=dict(sorted(category_counts.items())),
            )
        )
    summaries.sort(key=lambda item: (-item.avg_loose_score, -item.avg_graph_support, item.source))
    return summaries


def _top_categories(counts: dict[str, int], *, limit: int = 4) -> str:
    ranked = sorted(counts.items(), key=lambda item: (-item[1], item[0]))
    return ", ".join(f"{name}:{count}" for name, count in ranked[:limit])


def _markdown_cell(value: str) -> str:
    return value.replace("|", "\\|").replace("\n", " ")
