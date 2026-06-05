from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class FeatureWorkflowScore:
    candidate_name: str
    feature_labels: tuple[str, ...]
    workflow_labels: tuple[str, ...]
    matched_hints: tuple[str, ...]
    score: float
    rationale: str


def score_feature_workflow_hypothesis(
    *,
    candidate_name: str,
    candidate_keywords: tuple[str, ...],
    operator_hints: tuple[str, ...],
) -> FeatureWorkflowScore:
    normalized_keywords = tuple(keyword.lower() for keyword in candidate_keywords if keyword)

    matched_hints = tuple(
        hint
        for hint in operator_hints
        if any(keyword in hint.lower() for keyword in normalized_keywords)
    )

    feature_labels = tuple(
        keyword
        for keyword in normalized_keywords
        if any(keyword in hint.lower() for hint in matched_hints)
    )

    workflow_labels = tuple(
        hint for hint in matched_hints if "workflow" in hint.lower() or "scheduled" in hint.lower()
    )

    score = 1.0 if matched_hints else 0.0

    return FeatureWorkflowScore(
        candidate_name=candidate_name,
        feature_labels=feature_labels,
        workflow_labels=workflow_labels,
        matched_hints=matched_hints,
        score=score,
        rationale=(
            f"{candidate_name} matched {len(matched_hints)} feature/workflow operator hint(s)."
        ),
    )
