from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass(frozen=True)
class EvidenceItem:
    """One provenance-backed observation used by the RCA graph verifier."""

    id: str
    name: str
    modality: str
    summary: str
    command: str = ""
    raw_ref: str = ""
    score: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class RootHypothesis:
    """A graph-derived root-cause candidate with compact support evidence."""

    id: str
    kind: str
    label: str
    root_layer: str
    score: float
    reason: str
    entities: dict[str, list[str]] = field(default_factory=dict)
    modalities: list[str] = field(default_factory=list)
    support: list[EvidenceItem] = field(default_factory=list)
    contradictions: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["support"] = [item.to_dict() for item in self.support]
        return payload


@dataclass(frozen=True)
class EvidenceBundle:
    """Compact ontology view for one RealRCA case."""

    case_id: str
    split: str
    case_type: str
    data_ref: str
    ontology: list[str]
    retrieval_summary: str
    evidence: list[EvidenceItem]
    hypotheses: list[RootHypothesis]

    def to_dict(self) -> dict[str, Any]:
        return {
            "case_id": self.case_id,
            "split": self.split,
            "case_type": self.case_type,
            "data_ref": self.data_ref,
            "ontology": list(self.ontology),
            "retrieval_summary": self.retrieval_summary,
            "evidence": [item.to_dict() for item in self.evidence],
            "hypotheses": [item.to_dict() for item in self.hypotheses],
        }


@dataclass(frozen=True)
class CandidateAnswer:
    """One candidate answer row from a RealRCA result file."""

    source: str
    case_id: str
    diagnosis_output: str
    trace_id: str

    def to_result_row(self) -> dict[str, str]:
        return {
            "case_id": self.case_id,
            "diagnosis_output": self.diagnosis_output,
            "trace_id": self.trace_id,
        }


@dataclass(frozen=True)
class CandidateScore:
    """Verifier support score for one answer against one evidence bundle."""

    source: str
    graph_support: float
    answer_contract_score: float
    best_hypothesis_id: str
    best_hypothesis_label: str
    overlap_count: int
    modality_count: int
    novelty: float
    baseline_retention: float
    dropped_baseline_tokens: list[str]
    risk_flags: list[str]
    contract_flags: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class CandidateDecision:
    """Auditable selection result for one case."""

    case_id: str
    selected: CandidateAnswer
    baseline: CandidateAnswer
    accepted_replacement: bool
    reason: str
    scores: list[CandidateScore]

    def to_dict(self) -> dict[str, Any]:
        return {
            "case_id": self.case_id,
            "selected_source": self.selected.source,
            "baseline_source": self.baseline.source,
            "accepted_replacement": self.accepted_replacement,
            "reason": self.reason,
            "selected": self.selected.to_result_row(),
            "scores": [item.to_dict() for item in self.scores],
        }
