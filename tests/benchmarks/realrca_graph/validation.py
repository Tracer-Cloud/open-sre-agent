from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from tests.benchmarks.realrca_graph.features import token_features
from tests.benchmarks.realrca_graph.io import DATASET_DIR, load_json, rows_by_case, write_json
from tests.benchmarks.realrca_graph.models import CandidateAnswer


@dataclass(frozen=True)
class ValidationCaseScore:
    """Weak public-validation score for one answer."""

    case_id: str
    case_type: str
    loose_score: float
    critical_coverage: float
    item_coverage: float
    token_recall: float
    token_precision: float
    missing_critical_items: list[str]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class ValidationSummary:
    """Aggregate weak-score report for a validation result file."""

    result_path: str
    case_count: int
    avg_loose_score: float
    avg_critical_coverage: float
    cases: list[ValidationCaseScore]

    def to_dict(self) -> dict[str, Any]:
        return {
            "result_path": self.result_path,
            "hidden_reference_used": False,
            "validation_truth_used": True,
            "case_count": self.case_count,
            "avg_loose_score": self.avg_loose_score,
            "avg_critical_coverage": self.avg_critical_coverage,
            "cases": [case.to_dict() for case in self.cases],
        }


def _truth_rows(dataset_dir: Path = DATASET_DIR) -> dict[str, dict[str, Any]]:
    return {
        row["case_id"]: row
        for row in load_json(dataset_dir / "validation_ground_truth.json")
        if isinstance(row, dict) and isinstance(row.get("case_id"), str)
    }


def _case_meta(dataset_dir: Path = DATASET_DIR) -> dict[str, dict[str, Any]]:
    return {
        row["case_id"]: row
        for row in load_json(dataset_dir / "validation.json")
        if isinstance(row, dict) and isinstance(row.get("case_id"), str)
    }


def _required_items(truth: dict[str, Any]) -> list[dict[str, Any]]:
    reference = truth.get("reference") if isinstance(truth.get("reference"), dict) else {}
    items = (
        reference.get("required_items") if isinstance(reference.get("required_items"), list) else []
    )
    return [item for item in items if isinstance(item, dict)]


def _score_required_item(answer_tokens: set[str], item: dict[str, Any]) -> float:
    item_tokens = token_features(item)
    if not item_tokens:
        return 0.0
    overlap = answer_tokens & item_tokens
    if not overlap:
        return 0.0
    return min(1.0, len(overlap) / max(1.0, min(10.0, len(item_tokens))))


def score_validation_answer(
    answer: CandidateAnswer,
    truth: dict[str, Any],
    *,
    case_type: str = "",
) -> ValidationCaseScore:
    """Compute a weak local score using only public validation truth."""

    answer_tokens = token_features(answer.diagnosis_output)
    truth_tokens = token_features(
        {
            "chain": truth.get("root_cause_chain"),
            "reference": truth.get("reference"),
        }
    )
    overlap = answer_tokens & truth_tokens
    token_recall = len(overlap) / max(1, len(truth_tokens))
    token_precision = len(overlap) / max(1, len(answer_tokens))
    required_scores: list[tuple[dict[str, Any], float]] = [
        (item, _score_required_item(answer_tokens, item)) for item in _required_items(truth)
    ]
    critical_items = [(item, score) for item, score in required_scores if item.get("critical")]
    critical_coverage = (
        sum(1 for item, score in critical_items if score >= 0.18) / len(critical_items)
        if critical_items
        else 0.0
    )
    item_coverage = (
        sum(
            1
            for item, score in required_scores
            if score >= (0.18 if item.get("critical") else 0.12)
        )
        / len(required_scores)
        if required_scores
        else 0.0
    )
    loose_score = (
        0.55 * critical_coverage + 0.25 * item_coverage + 0.2 * min(1.0, token_recall * 3.0)
    )
    missing_critical = [
        str(item.get("name") or item.get("description") or "critical_item")
        for item, score in critical_items
        if score < 0.18
    ]
    return ValidationCaseScore(
        case_id=answer.case_id,
        case_type=case_type,
        loose_score=round(loose_score, 4),
        critical_coverage=round(critical_coverage, 4),
        item_coverage=round(item_coverage, 4),
        token_recall=round(token_recall, 4),
        token_precision=round(token_precision, 4),
        missing_critical_items=missing_critical,
    )


def score_validation_file(
    result_path: Path, *, dataset_dir: Path = DATASET_DIR
) -> ValidationSummary:
    truths = _truth_rows(dataset_dir)
    metas = _case_meta(dataset_dir)
    rows = rows_by_case(result_path)
    scores: list[ValidationCaseScore] = []
    for case_id, truth in truths.items():
        answer = rows.get(case_id)
        if answer is None:
            continue
        meta = metas.get(case_id, {})
        scores.append(score_validation_answer(answer, truth, case_type=str(meta.get("type") or "")))
    scores.sort(key=lambda item: (item.loose_score, item.case_id))
    return ValidationSummary(
        result_path=str(result_path),
        case_count=len(scores),
        avg_loose_score=round(sum(item.loose_score for item in scores) / max(1, len(scores)), 4),
        avg_critical_coverage=round(
            sum(item.critical_coverage for item in scores) / max(1, len(scores)), 4
        ),
        cases=scores,
    )


def write_validation_summary(summary: ValidationSummary, path: Path) -> None:
    write_json(path, summary.to_dict())
