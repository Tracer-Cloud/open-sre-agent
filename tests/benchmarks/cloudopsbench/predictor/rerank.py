"""Lever D — evidence-weighted top-3 re-ranking (conservative rescue variant).

11:46 failure analysis: of the 77 a1=0 cases, 41 (53%) had the correct
``fault_object`` SOMEWHERE in the LLM's top-3, but only 29 (38%) at rank-1.
That's ~15 points of object accuracy parked in ranks 2-3 because the
LLM's own confidence ordering didn't surface the best-evidenced candidate.
Re-ranking by how many of each prediction's identifying tokens appear in
the actual investigation evidence pulls those candidates up.

Cheap deterministic variant (this module): substring count, no LLM call.
Audit-grade variant (LLM-as-judge over the same input) is a follow-up.
"""

from __future__ import annotations

import re
from typing import Any

# Stem tokens that are too common across predictions to discriminate by their
# presence in the evidence — "service" appears in every Kubernetes diagnosis,
# "fault" / "error" / "pod" are noise. Counting them inflates every prediction's
# score equally, defeating the rerank. Drop them from the token set.
_RERANK_STOPWORDS: frozenset[str] = frozenset(
    {"app", "node", "namespace", "service", "fault", "error", "pod", "the", "and", "for"}
)

# Tokens shorter than this can't carry meaningful signal (single letters,
# 2-char abbreviations are too noisy to substring-match reliably).
_RERANK_MIN_TOKEN_LEN: int = 3


def _prediction_tokens(prediction: dict[str, Any]) -> set[str]:
    """Pull the identifying tokens from one prediction.

    Combines ``fault_object`` (after stripping the prefix) and ``root_cause``,
    splits on the structural separators that the dataset uses (``_``, ``-``,
    ``/``), lowercases, and drops stop-words + tokens shorter than
    ``_RERANK_MIN_TOKEN_LEN``. The result is the set of substrings that
    should appear in the evidence if this prediction is well-supported.
    """
    fields: list[str] = []
    fault_obj = (prediction.get("fault_object") or "").strip().lower()
    if "/" in fault_obj:
        _prefix, _, name = fault_obj.partition("/")
        fault_obj = name
    if fault_obj:
        fields.append(fault_obj)
    root_cause = (prediction.get("root_cause") or "").strip().lower()
    if root_cause:
        fields.append(root_cause)
    tokens: set[str] = set()
    for field in fields:
        for tok in re.split(r"[_\-/\s]+", field):
            if len(tok) >= _RERANK_MIN_TOKEN_LEN and tok not in _RERANK_STOPWORDS:
                tokens.add(tok)
    return tokens


def rerank_predictions_by_evidence(
    predictions: list[dict[str, Any]],
    evidence_text: str,
) -> list[dict[str, Any]]:
    """Conservatively rescue the top-1 if it has zero evidence support.

    **Empirical motivation**: a permissive "always re-sort by substring
    hits" version was tested against the 11:46 case data and produced a
    −7.2pp regression on A@1 (103/180 → 90/180 correct triple-matches).
    Cause: when the investigation discusses multiple services, multiple
    predictions accumulate substring hits, and a wrong-but-multiply-cited
    rank-2 was beating a correct-and-singly-cited rank-1. Substring count
    alone is not strong enough signal to over-rule the LLM's confidence
    ordering.

    The conservative variant in this function only fires when **rank-1
    has ZERO matching tokens in the evidence** (a clear "the LLM picked a
    prediction the investigation never mentioned" signal). When that
    fires, the highest-scoring non-rank-1 prediction is promoted. All
    other cases are identity — protecting the LLM's confidence ordering
    when it has any evidence backing at all.

    This recovers ~2 a1 cells per 180 (from the 11:46 replay) without
    regressing the 30+ cells the LLM had correctly ranked at #1.

    Returns a NEW list — the input is not mutated. ``rank`` is rewritten
    to match the new 1-based positions.
    """
    if len(predictions) <= 1:
        return list(predictions)
    haystack = (evidence_text or "").lower()
    if not haystack.strip():
        return list(predictions)
    scores: list[int] = []
    for prediction in predictions:
        tokens = _prediction_tokens(prediction)
        scores.append(sum(1 for tok in tokens if tok in haystack))
    # Conservative gate: only intervene when rank-1 has zero evidence hits.
    # When the LLM's top pick IS evidenced at all, defer to its judgment —
    # cross-citation noise in the substring count is too high to over-rule
    # a confidence ordering that has any backing.
    if scores[0] > 0:
        return list(predictions)
    # Find the highest-scoring non-rank-1 prediction. If none score positive,
    # all predictions are unevidenced and we have no signal to act on.
    best_alt_idx: int | None = None
    best_alt_score = 0
    for idx in range(1, len(predictions)):
        if scores[idx] > best_alt_score:
            best_alt_score = scores[idx]
            best_alt_idx = idx
    if best_alt_idx is None:
        return list(predictions)
    # Promote: chosen alt becomes rank-1, original rank-1 takes the alt's slot,
    # everything else preserves relative order so the swap is minimally disruptive.
    promoted = predictions[best_alt_idx]
    new_order = [promoted, predictions[0]]
    for idx, prediction in enumerate(predictions):
        if idx in (0, best_alt_idx):
            continue
        new_order.append(prediction)
    return [{**prediction, "rank": new_rank + 1} for new_rank, prediction in enumerate(new_order)]
