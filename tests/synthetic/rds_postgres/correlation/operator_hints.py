from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class OperatorHintScore:
    candidate_name: str
    matched_hints: tuple[str, ...]
    score: float
    rationale: str


def score_operator_hints(
    *,
    candidate_name: str,
    candidate_keywords: tuple[str, ...],
    operator_hints: tuple[str, ...],
) -> OperatorHintScore:
    normalized_keywords = tuple(keyword.lower() for keyword in candidate_keywords)
    matched = tuple(
        hint
        for hint in operator_hints
        if any(keyword in hint.lower() for keyword in normalized_keywords)
    )

    score = 1.0 if matched else 0.0

    return OperatorHintScore(
        candidate_name=candidate_name,
        matched_hints=matched,
        score=score,
        rationale=(f"{candidate_name} matched {len(matched)} operator hint(s)."),
    )
