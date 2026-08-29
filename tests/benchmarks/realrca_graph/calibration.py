from __future__ import annotations

from collections import Counter
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from tests.benchmarks.realrca_graph.bundle_cache import build_evidence_bundle_cached
from tests.benchmarks.realrca_graph.features import clip_text, token_features
from tests.benchmarks.realrca_graph.io import (
    DATASET_DIR,
    DEFAULT_GRAPH_ROOT,
    graph_context_path,
    load_cases,
    load_json,
)
from tests.benchmarks.realrca_graph.models import RootHypothesis

CRITICAL_MECHANISM_GROUPS = {
    "cache",
    "change",
    "consume_failure",
    "connection_pool",
    "data_quality",
    "hardware",
    "host",
    "limit",
    "memory",
    "master_data",
    "mq",
    "network",
    "pod",
    "security",
    "sql",
    "thread_pool",
    "timeout",
    "traffic_source",
}
CRITICAL_MECHANISM_SCORE = 0.35


@dataclass(frozen=True)
class HypothesisCalibration:
    """Public-validation score for one graph-derived hypothesis."""

    rank: int
    hypothesis_id: str
    kind: str
    label: str
    root_layer: str
    graph_score: float
    modalities: list[str]
    overlap_count: int
    token_recall: float
    critical_item_coverage: float
    hit_critical_items: list[str]
    missing_critical_items: list[str]
    hit: bool
    contradictions: list[str]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class CaseCalibration:
    """Public-validation calibration result for one case."""

    case_id: str
    case_type: str
    graph_path: str | None
    truth_preview: str
    top_hit_rank: int | None
    hypotheses: list[HypothesisCalibration]

    def to_dict(self) -> dict[str, Any]:
        return {
            "case_id": self.case_id,
            "case_type": self.case_type,
            "graph_path": self.graph_path,
            "truth_preview": self.truth_preview,
            "top_hit_rank": self.top_hit_rank,
            "hypotheses": [hypothesis.to_dict() for hypothesis in self.hypotheses],
        }


@dataclass(frozen=True)
class CalibrationReport:
    """Aggregate public-validation report for graph/ontology ranking."""

    split: str
    graph_roots: list[str]
    case_count: int
    missing_graph_count: int
    top1_hit_rate: float
    top3_hit_rate: float
    mean_reciprocal_rank: float
    type_counts: dict[str, int]
    type_top1_hit_rates: dict[str, float]
    cases: list[CaseCalibration]

    def to_dict(self) -> dict[str, Any]:
        return {
            "split": self.split,
            "graph_root": self.graph_roots[0] if self.graph_roots else "",
            "graph_roots": list(self.graph_roots),
            "case_count": self.case_count,
            "missing_graph_count": self.missing_graph_count,
            "public_validation_truth_used": True,
            "hidden_test_reference_used": False,
            "top1_hit_rate": self.top1_hit_rate,
            "top3_hit_rate": self.top3_hit_rate,
            "mean_reciprocal_rank": self.mean_reciprocal_rank,
            "type_counts": dict(self.type_counts),
            "type_top1_hit_rates": dict(self.type_top1_hit_rates),
            "cases": [case.to_dict() for case in self.cases],
        }


def build_calibration_report(
    *,
    graph_roots: list[Path],
    split: str = "validation",
    dataset_dir: Path = DATASET_DIR,
    hypothesis_limit: int = 10,
    min_overlap: int = 2,
    min_recall: float = 0.08,
) -> CalibrationReport:
    """Calibrate graph hypothesis ranking against public validation truth."""

    roots = graph_roots or [DEFAULT_GRAPH_ROOT]
    truth_by_case = _truth_rows(dataset_dir)
    case_meta = {
        str(row.get("case_id")): row
        for row in load_cases(split, dataset_dir)
        if isinstance(row, dict) and isinstance(row.get("case_id"), str)
    }
    cases: list[CaseCalibration] = []
    missing_graph_count = 0

    for case_id, truth in truth_by_case.items():
        graph_path = _find_graph_context_path(roots, split, case_id)
        case_type = str((case_meta.get(case_id) or {}).get("type") or "")
        if graph_path is None:
            missing_graph_count += 1
            cases.append(
                CaseCalibration(
                    case_id=case_id,
                    case_type=case_type,
                    graph_path=None,
                    truth_preview=clip_text(_truth_text(truth), 240),
                    top_hit_rank=None,
                    hypotheses=[],
                )
            )
            continue
        bundle = build_evidence_bundle_cached(graph_path, hypothesis_limit=hypothesis_limit)
        truth_tokens = token_features(_truth_text(truth))
        critical_items = _critical_required_items(truth)
        calibrated = [
            _score_hypothesis(
                rank=index,
                hypothesis=hypothesis,
                truth_tokens=truth_tokens,
                critical_items=critical_items,
                min_overlap=min_overlap,
                min_recall=min_recall,
            )
            for index, hypothesis in enumerate(bundle.hypotheses, start=1)
        ]
        top_hit_rank = next((item.rank for item in calibrated if item.hit), None)
        cases.append(
            CaseCalibration(
                case_id=case_id,
                case_type=case_type or bundle.case_type,
                graph_path=str(graph_path),
                truth_preview=clip_text(_truth_text(truth), 240),
                top_hit_rank=top_hit_rank,
                hypotheses=calibrated,
            )
        )

    case_count = len(cases)
    top1 = sum(1 for case in cases if case.top_hit_rank == 1)
    top3 = sum(1 for case in cases if case.top_hit_rank is not None and case.top_hit_rank <= 3)
    reciprocal = [1.0 / case.top_hit_rank for case in cases if case.top_hit_rank is not None]
    type_counts = Counter(case.case_type or "unknown" for case in cases)
    type_top1_hits = Counter(
        case.case_type or "unknown" for case in cases if case.top_hit_rank == 1
    )
    cases.sort(key=lambda item: (item.top_hit_rank is None, item.top_hit_rank or 99, item.case_id))
    return CalibrationReport(
        split=split,
        graph_roots=[str(root) for root in roots],
        case_count=case_count,
        missing_graph_count=missing_graph_count,
        top1_hit_rate=round(top1 / max(1, case_count), 4),
        top3_hit_rate=round(top3 / max(1, case_count), 4),
        mean_reciprocal_rank=round(sum(reciprocal) / max(1, case_count), 4),
        type_counts=dict(sorted(type_counts.items())),
        type_top1_hit_rates={
            case_type: round(type_top1_hits[case_type] / count, 4)
            for case_type, count in sorted(type_counts.items())
        },
        cases=cases,
    )


def render_calibration_markdown(report: CalibrationReport, *, limit: int = 40) -> str:
    lines = [
        "# RealRCA Graph/Ontology Calibration",
        "",
        f"- split: `{report.split}`",
        f"- cases: `{report.case_count}`",
        f"- missing_graphs: `{report.missing_graph_count}`",
        f"- graph_roots: `{report.graph_roots}`",
        f"- top1_hit_rate: `{report.top1_hit_rate}`",
        f"- top3_hit_rate: `{report.top3_hit_rate}`",
        f"- mrr: `{report.mean_reciprocal_rank}`",
        f"- type_top1_hit_rates: `{report.type_top1_hit_rates}`",
        "",
        "| case | type | hit_rank | top hypothesis | hit | truth |",
        "| --- | --- | ---: | --- | --- | --- |",
    ]
    for case in report.cases[:limit]:
        top = case.hypotheses[0] if case.hypotheses else None
        lines.append(
            "| "
            + " | ".join(
                [
                    f"`{case.case_id[-4:]}`",
                    case.case_type or "-",
                    str(case.top_hit_rank or "-"),
                    _markdown_cell(top.label if top else "-"),
                    "yes" if case.top_hit_rank == 1 else "no",
                    _markdown_cell(case.truth_preview),
                ]
            )
            + " |"
        )
    return "\n".join(lines) + "\n"


def _score_hypothesis(
    *,
    rank: int,
    hypothesis: RootHypothesis,
    truth_tokens: set[str],
    critical_items: list[dict[str, Any]],
    min_overlap: int,
    min_recall: float,
) -> HypothesisCalibration:
    hypothesis_tokens = _hypothesis_match_tokens(hypothesis)
    overlap = hypothesis_tokens & truth_tokens
    recall = len(overlap) / max(1, len(truth_tokens))
    item_scores = [
        (
            str(item.get("name") or item.get("description") or "critical_item"),
            _score_item(hypothesis_tokens, item),
        )
        for item in critical_items
    ]
    hit_items = [name for name, score in item_scores if score >= 0.35]
    missing_items = [name for name, score in item_scores if score < 0.35]
    critical_coverage = len(hit_items) / max(1, len(item_scores)) if item_scores else 0.0
    if critical_items:
        hit = bool(hit_items)
    else:
        hit = len(overlap) >= min_overlap or recall >= min_recall
    return HypothesisCalibration(
        rank=rank,
        hypothesis_id=hypothesis.id,
        kind=hypothesis.kind,
        label=hypothesis.label,
        root_layer=hypothesis.root_layer,
        graph_score=hypothesis.score,
        modalities=list(hypothesis.modalities),
        overlap_count=len(overlap),
        token_recall=round(recall, 4),
        critical_item_coverage=round(critical_coverage, 4),
        hit_critical_items=hit_items,
        missing_critical_items=missing_items,
        hit=hit,
        contradictions=list(hypothesis.contradictions),
    )


def _truth_rows(dataset_dir: Path) -> dict[str, dict[str, Any]]:
    return {
        row["case_id"]: row
        for row in load_json(dataset_dir / "validation_ground_truth.json")
        if isinstance(row, dict) and isinstance(row.get("case_id"), str)
    }


def _hypothesis_match_tokens(hypothesis: RootHypothesis) -> set[str]:
    return token_features(
        {
            "kind": hypothesis.kind,
            "label": hypothesis.label,
            "root_layer": hypothesis.root_layer,
            "reason": hypothesis.reason,
            "entities": hypothesis.entities,
        }
    )


def _truth_text(truth: dict[str, Any]) -> dict[str, Any]:
    return {
        "root_cause_chain": truth.get("root_cause_chain"),
        "reference": truth.get("reference"),
    }


def _critical_required_items(truth: dict[str, Any]) -> list[dict[str, Any]]:
    reference = truth.get("reference") if isinstance(truth.get("reference"), dict) else {}
    items = (
        reference.get("required_items") if isinstance(reference.get("required_items"), list) else []
    )
    return [item for item in items if isinstance(item, dict) and item.get("critical")]


def _score_item(hypothesis_tokens: set[str], item: dict[str, Any]) -> float:
    item_tokens = token_features(item)
    if not item_tokens:
        return 0.0
    overlap = hypothesis_tokens & item_tokens
    exact_score = len(overlap) / min(10.0, len(item_tokens))
    if _has_strong_entity_overlap(overlap):
        exact_score = max(exact_score, CRITICAL_MECHANISM_SCORE)
    item_groups = _critical_mechanism_groups(item_tokens)
    hypothesis_groups = _critical_mechanism_groups(hypothesis_tokens)
    if item_groups & hypothesis_groups:
        return max(exact_score, CRITICAL_MECHANISM_SCORE)
    if item_groups:
        return min(exact_score, CRITICAL_MECHANISM_SCORE - 0.01)
    return exact_score


def _has_strong_entity_overlap(tokens: set[str]) -> bool:
    for token in tokens:
        prefix, _, value = token.partition(":")
        if prefix not in {"term", "sql_table", "service", "exception", "rds"}:
            continue
        if len(value) >= 12 and ("_" in value or "." in value or prefix != "term"):
            return True
    return False


def _critical_mechanism_groups(tokens: set[str]) -> set[str]:
    prefix = "keyword:"
    return {
        token.removeprefix(prefix)
        for token in tokens
        if token.startswith(prefix) and token.removeprefix(prefix) in CRITICAL_MECHANISM_GROUPS
    }


def _find_graph_context_path(graph_roots: list[Path], split: str, case_id: str) -> Path | None:
    for root in graph_roots:
        path = graph_context_path(root, split, case_id)
        if path.exists():
            return path
    return None


def _markdown_cell(value: str) -> str:
    return value.replace("|", "\\|").replace("\n", " ")
