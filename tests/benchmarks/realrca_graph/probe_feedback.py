from __future__ import annotations

import re
from collections import Counter
from dataclasses import asdict, dataclass
from typing import Any

_CASE_SUFFIX_RE = re.compile(r"^[0-9a-f]{4}$", re.IGNORECASE)
_TOKEN_RE = re.compile(r"[a-z0-9]+")
_STOP_TOKENS = {
    "agent",
    "best",
    "best8384",
    "best8485",
    "case",
    "current",
    "plus",
    "probe",
    "result",
    "results",
    "submission",
    "test",
}


@dataclass(frozen=True)
class ProbeRecord:
    """One public leaderboard row mapped to a case suffix."""

    suffix: str
    agent_name: str
    accuracy: float
    delta: float
    outcome: str
    coverage: float | None = None
    quality_score: float | None = None
    family_markers: list[str] | None = None

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["family_markers"] = list(self.family_markers or [])
        return payload


@dataclass(frozen=True)
class CaseProbeFeedback:
    """Probe history for all leaderboard rows that target one case suffix."""

    suffix: str
    reference_accuracy: float
    records: list[ProbeRecord]

    @property
    def negative_count(self) -> int:
        return sum(1 for item in self.records if item.outcome == "negative")

    @property
    def positive_count(self) -> int:
        return sum(1 for item in self.records if item.outcome == "positive")

    @property
    def neutral_count(self) -> int:
        return sum(1 for item in self.records if item.outcome == "neutral")

    @property
    def best_delta(self) -> float | None:
        if not self.records:
            return None
        return max(item.delta for item in self.records)

    @property
    def worst_delta(self) -> float | None:
        if not self.records:
            return None
        return min(item.delta for item in self.records)

    def matching_negative(self, source: str, *, max_delta: float = -0.05) -> ProbeRecord | None:
        """Return a negative probe that appears to share the candidate source family."""

        source_markers = _source_markers(source)
        return self.matching_negative_markers(source_markers, max_delta=max_delta)

    def matching_negative_markers(
        self,
        markers: set[str],
        *,
        max_delta: float = -0.05,
    ) -> ProbeRecord | None:
        """Return a negative probe whose agent-name markers overlap ``markers``."""

        if not markers:
            return None
        for record in self.records:
            if record.delta > max_delta:
                continue
            record_markers = set(record.family_markers or [])
            if markers & record_markers:
                return record
        return None

    def to_dict(self) -> dict[str, Any]:
        return {
            "suffix": self.suffix,
            "reference_accuracy": self.reference_accuracy,
            "negative_count": self.negative_count,
            "positive_count": self.positive_count,
            "neutral_count": self.neutral_count,
            "best_delta": self.best_delta,
            "worst_delta": self.worst_delta,
            "records": [item.to_dict() for item in self.records],
        }


@dataclass(frozen=True)
class ProbeFeedbackLedger:
    """Public leaderboard feedback grouped by RealRCA case suffix."""

    team_name: str
    reference_accuracy: float
    cases: dict[str, CaseProbeFeedback]

    @classmethod
    def from_leaderboard(
        cls,
        payload: dict[str, Any],
        *,
        team_name: str,
        reference_accuracy: float | None = None,
        neutral_epsilon: float = 0.05,
    ) -> ProbeFeedbackLedger:
        rows = [
            item
            for item in payload.get("items", [])
            if isinstance(item, dict) and item.get("team_name") == team_name
        ]
        accuracies = [_as_float(item.get("accuracy")) for item in rows]
        numeric_accuracies = [item for item in accuracies if item is not None]
        reference = reference_accuracy
        if reference is None:
            reference = max(numeric_accuracies) if numeric_accuracies else 0.0

        grouped: dict[str, list[ProbeRecord]] = {}
        for item in rows:
            agent_name = str(item.get("agent_name") or "")
            suffix = probe_suffix(agent_name)
            accuracy = _as_float(item.get("accuracy"))
            if not suffix or accuracy is None:
                continue
            delta = round(accuracy - reference, 4)
            if delta > neutral_epsilon:
                outcome = "positive"
            elif delta < -neutral_epsilon:
                outcome = "negative"
            else:
                outcome = "neutral"
            record = ProbeRecord(
                suffix=suffix,
                agent_name=agent_name,
                accuracy=accuracy,
                delta=delta,
                outcome=outcome,
                coverage=_as_float(item.get("coverage")),
                quality_score=_as_float(item.get("quality_score")),
                family_markers=sorted(_family_markers(agent_name, suffix)),
            )
            grouped.setdefault(suffix, []).append(record)

        cases = {
            suffix: CaseProbeFeedback(
                suffix=suffix,
                reference_accuracy=reference,
                records=sorted(records, key=lambda item: (-item.accuracy, item.agent_name)),
            )
            for suffix, records in grouped.items()
        }
        return cls(team_name=team_name, reference_accuracy=reference, cases=cases)

    def for_case_id(self, case_id: str) -> CaseProbeFeedback | None:
        return self.cases.get(case_suffix(case_id))

    def to_dict(self) -> dict[str, Any]:
        outcomes = Counter(
            record.outcome for feedback in self.cases.values() for record in feedback.records
        )
        return {
            "team_name": self.team_name,
            "reference_accuracy": self.reference_accuracy,
            "case_count": len(self.cases),
            "outcomes": dict(outcomes),
            "cases": {suffix: feedback.to_dict() for suffix, feedback in self.cases.items()},
        }


def case_suffix(case_id: str) -> str:
    """Return the stable four-character suffix used in probe names."""

    tail = case_id.rsplit("-", 1)[-1]
    return tail[-4:].lower()


def probe_suffix(agent_name: str) -> str:
    """Extract a RealRCA case suffix from a leaderboard agent name."""

    for token in reversed(agent_name.lower().split("-")):
        if len(token) == 5 and token.startswith("321"):
            return token[-4:]
        if _CASE_SUFFIX_RE.fullmatch(token):
            return token
    return ""


def _as_float(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _family_markers(agent_name: str, suffix: str) -> set[str]:
    tokens = [
        token
        for token in _TOKEN_RE.findall(agent_name.lower())
        if token and token not in _STOP_TOKENS and token != suffix and token != f"321{suffix}"
    ]
    return _marker_set(tokens) | _semantic_markers(tokens)


def _source_markers(source: str) -> set[str]:
    tokens = [
        token for token in _TOKEN_RE.findall(source.lower()) if token and token not in _STOP_TOKENS
    ]
    return _marker_set(tokens) | _semantic_markers(tokens)


def _marker_set(tokens: list[str]) -> set[str]:
    markers: set[str] = set()
    for token in tokens:
        if len(token) >= 4:
            markers.add(token)
    for size in (2, 3):
        for index in range(0, max(0, len(tokens) - size + 1)):
            joined = "".join(tokens[index : index + size])
            if len(joined) >= 6:
                markers.add(joined)
    compact = "".join(tokens)
    if len(compact) >= 6:
        markers.add(compact)
    return markers


def _semantic_markers(tokens: list[str]) -> set[str]:
    markers: set[str] = set()
    token_set = set(tokens)
    has_trajectory = bool(token_set & {"traj", "trajectory"}) or any(
        token.startswith("traj") for token in token_set
    )
    has_patch = any("patch" in token for token in token_set)
    if has_trajectory and has_patch:
        markers.update({"trajpatch", "trajectorypatch"})
    return markers
