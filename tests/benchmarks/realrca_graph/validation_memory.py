from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from tests.benchmarks.realrca_graph.features import clip_text, token_features
from tests.benchmarks.realrca_graph.io import REALRCA_GRAPH, load_json
from tests.benchmarks.realrca_graph.models import CandidateAnswer, EvidenceBundle

DEFAULT_VALIDATION_MEMORY = REALRCA_GRAPH / "validation_case_memory_index.json"
USEFUL_TOKEN_PREFIXES = (
    "app:",
    "service:",
    "method:",
    "exception:",
    "rds:",
    "sql_",
    "keyword:",
)
NOISY_VALIDATION_TOKENS = {
    "app:alibaba-inc",
    "app:aserver-ingress-host",
    "app:aserver-ingress-tao-host",
    "app:center-zb",
    "app:multi-signal",
}


@dataclass(frozen=True)
class ValidationExemplarMatch:
    """Public validation exemplar retrieved for one test bundle."""

    case_id: str
    case_type: str
    similarity: float
    overlap_count: int
    root_summary: str
    graph_summary: str
    matched_terms: list[str]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def load_validation_memory(path: Path = DEFAULT_VALIDATION_MEMORY) -> dict[str, Any] | None:
    """Load the public validation case-memory index when present."""

    if not path.exists():
        return None
    payload = load_json(path)
    return payload if isinstance(payload, dict) else None


def match_validation_exemplars(
    bundle: EvidenceBundle,
    memory: dict[str, Any] | None,
    *,
    answer: CandidateAnswer | None = None,
    limit: int = 3,
) -> list[ValidationExemplarMatch]:
    """Find public validation cases with similar typed evidence and root patterns."""

    if not memory:
        return []
    entries = memory.get("entries")
    if not isinstance(entries, list):
        return []
    query_tokens = _bundle_tokens(bundle, answer=answer)
    if not query_tokens:
        return []
    matches: list[ValidationExemplarMatch] = []
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        tokens = _entry_tokens(entry)
        overlap = query_tokens & tokens
        if not overlap:
            continue
        case_type = str(entry.get("case_type") or "")
        type_bonus = 0.18 if case_type == bundle.case_type else 0.0
        similarity = len(overlap) / max(len(query_tokens), 1) + type_bonus
        truth = entry.get("truth") if isinstance(entry.get("truth"), dict) else {}
        graph = entry.get("graph") if isinstance(entry.get("graph"), dict) else {}
        matches.append(
            ValidationExemplarMatch(
                case_id=str(entry.get("case_id") or ""),
                case_type=case_type,
                similarity=round(similarity, 4),
                overlap_count=len(overlap),
                root_summary=_truth_root_summary(truth),
                graph_summary=clip_text(str(graph.get("retrieval_summary") or ""), 420),
                matched_terms=sorted(overlap)[:16],
            )
        )
    matches.sort(key=lambda item: (-item.similarity, -item.overlap_count, item.case_id))
    return matches[:limit]


def _bundle_tokens(bundle: EvidenceBundle, *, answer: CandidateAnswer | None = None) -> set[str]:
    payload: dict[str, Any] = {
        "case_type": bundle.case_type,
        "hypotheses": [
            {
                "kind": item.kind,
                "label": item.label,
                "root_layer": item.root_layer,
                "reason": item.reason,
                "entities": item.entities,
            }
            for item in bundle.hypotheses[:8]
        ],
        "evidence": [
            {
                "name": item.name,
                "modality": item.modality,
                "summary": item.summary,
            }
            for item in bundle.evidence[:24]
        ],
    }
    if answer is not None:
        payload["current_answer"] = {
            "diagnosis_output": answer.diagnosis_output,
            "trace_id": answer.trace_id,
        }
    return {token for token in token_features(payload) if _useful_token(token)}


def _entry_tokens(entry: dict[str, Any]) -> set[str]:
    raw_tokens = entry.get("feature_tokens")
    tokens = (
        {
            str(token)
            for token in raw_tokens
            if isinstance(token, str) and _useful_feature_token(token)
        }
        if isinstance(raw_tokens, list)
        else set()
    )
    truth = entry.get("truth") if isinstance(entry.get("truth"), dict) else {}
    tokens.update(token for token in token_features(truth) if _useful_token(token))
    return tokens


def _useful_feature_token(token: str) -> bool:
    if token.startswith("keyword:"):
        return False
    return _useful_token(token)


def _useful_token(token: str) -> bool:
    if token in NOISY_VALIDATION_TOKENS:
        return False
    if token.startswith("app:aserver"):
        return False
    return any(token.startswith(prefix) for prefix in USEFUL_TOKEN_PREFIXES)


def _truth_root_summary(truth: dict[str, Any]) -> str:
    chain = truth.get("root_cause_chain")
    if not isinstance(chain, list):
        return ""
    parts: list[str] = []
    for item in chain[:4]:
        if not isinstance(item, dict):
            continue
        item_type = str(item.get("type") or "")
        description = str(item.get("description") or "")
        component = item.get("component") if isinstance(item.get("component"), dict) else {}
        component_name = str(component.get("name") or "")
        component_type = str(component.get("type") or "")
        parts.append(
            " ".join(
                part
                for part in (
                    item_type,
                    description,
                    f"component={component_name}/{component_type}" if component_name else "",
                )
                if part
            )
        )
    return clip_text(" | ".join(parts), 520)
