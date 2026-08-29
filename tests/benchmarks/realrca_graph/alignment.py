from __future__ import annotations

from dataclasses import dataclass

from tests.benchmarks.realrca_graph.features import token_features
from tests.benchmarks.realrca_graph.models import CandidateAnswer

CRITICAL_PREFIXES = (
    "app:",
    "service:",
    "method:",
    "exception:",
    "rds:",
    "sql_id:",
    "sql_db:",
    "sql_table:",
    "sql_op:",
    "keyword:",
)

LOW_SIGNAL_TERMS = {"sql_id", "sqlid", "trace", "trace_id"}


@dataclass(frozen=True)
class AlignmentAssessment:
    """How much candidate text preserves a baseline answer's high-signal entities."""

    retention: float
    baseline_tokens: list[str]
    dropped_tokens: list[str]


def critical_tokens(answer: CandidateAnswer) -> set[str]:
    """Extract high-signal root-cause tokens while ignoring trace ids."""

    tokens = token_features(answer.diagnosis_output)
    critical = {token for token in tokens if token.startswith(CRITICAL_PREFIXES)}
    trace_values = {token.removeprefix("trace:") for token in tokens if token.startswith("trace:")}
    for token in tokens:
        if not token.startswith("term:"):
            continue
        value = token.removeprefix("term:")
        if value in LOW_SIGNAL_TERMS:
            continue
        if all(char in "0123456789abcdef" for char in value) and any(
            value in trace_id for trace_id in trace_values
        ):
            continue
        if "_" in value or "-" in value or any(char.isdigit() for char in value):
            critical.add(token)
    return critical


def assess_alignment(candidate: CandidateAnswer, baseline: CandidateAnswer) -> AlignmentAssessment:
    """Measure whether ``candidate`` drops critical baseline entities."""

    baseline_tokens = critical_tokens(baseline)
    if not baseline_tokens:
        return AlignmentAssessment(retention=1.0, baseline_tokens=[], dropped_tokens=[])

    candidate_tokens = critical_tokens(candidate)
    retained = baseline_tokens & candidate_tokens
    dropped = sorted(baseline_tokens - candidate_tokens)
    return AlignmentAssessment(
        retention=round(len(retained) / len(baseline_tokens), 4),
        baseline_tokens=sorted(baseline_tokens),
        dropped_tokens=dropped,
    )
