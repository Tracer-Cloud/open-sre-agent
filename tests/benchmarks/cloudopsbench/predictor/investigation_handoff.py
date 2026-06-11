"""B1 — align predictor rank-1 with investigation prose (opensre arm only).

The bench scores ``top_3_predictions[0]``, not the investigation RCA. When
the predictor LLM re-diagnoses from the alert and puts the investigation-
supported answer at rank-2, opensre loses a1 even though the investigation
was right (translation-loss).

This module is a deterministic post-pass on the predictor output. It only
runs when a non-empty ``investigation_summary`` is present (``opensre+llm``
path). Control arms with an empty summary are unchanged.

Promotion rule (mechanism-level, not per-case):
  - Score each prediction's ``root_cause`` by how many of its identifying
    tokens appear in the investigation text.
  - If a non-rank-1 prediction strictly outscores rank-1 AND meets a
    minimum support threshold, promote it to rank-1 (swap ordering).

This is stronger than ``rerank_predictions_by_evidence``'s conservative
gate (rank-1 must have *zero* hits). Here we promote when rank-2 is
*better evidenced* than rank-1 — the ``runtime/56`` failure class.
"""

from __future__ import annotations

import logging
import re
from typing import Any

from tests.benchmarks.cloudopsbench.scoring import _taxonomy_for_root_cause

logger = logging.getLogger(__name__)

# Tokens too generic to count as root-cause evidence in investigation prose.
_INVESTIGATION_STOPWORDS: frozenset[str] = frozenset(
    {
        "app",
        "node",
        "namespace",
        "service",
        "fault",
        "error",
        "pod",
        "the",
        "and",
        "for",
        "with",
        "from",
        "invalid",
        "incorrect",
        "missing",
        "failure",
        "mismatch",
    }
)

_INVESTIGATION_TOKEN_MIN_LEN: int = 4

# Require at least this many distinct root-cause tokens in the investigation
# before we override the predictor's confidence ordering.
_MIN_PROMOTION_SCORE: int = 2


def _root_cause_investigation_tokens(root_cause: str) -> set[str]:
    """Identifying tokens from a snapped root_cause for substring matching."""
    tokens: set[str] = set()
    for tok in re.split(r"[_\-/\s]+", (root_cause or "").strip().lower()):
        if len(tok) >= _INVESTIGATION_TOKEN_MIN_LEN and tok not in _INVESTIGATION_STOPWORDS:
            tokens.add(tok)
    return tokens


def _root_cause_investigation_score(haystack: str, root_cause: str) -> int:
    """Count how many root_cause tokens appear in investigation prose."""
    tokens = _root_cause_investigation_tokens(root_cause)
    if not tokens:
        return 0
    return sum(1 for tok in tokens if tok in haystack)


def align_predictions_to_investigation(
    predictions: list[dict[str, Any]],
    investigation_summary: str,
) -> list[dict[str, Any]]:
    """Promote a better-evidenced alt when rank-1 contradicts the investigation.

    Returns a new list; input is not mutated. ``rank`` fields are rewritten
  to match the new 1-based order. Taxonomy is re-derived from root_cause
    after any swap so the triple stays scorer-consistent.

    Args:
        predictions: cleaned top-3 from ``_parse_predictions`` (already snapped).
        investigation_summary: text from ``_summarize_investigation``; empty
            on control arms → caller should skip, but this function is a no-op
            on empty input anyway.
    """
    if len(predictions) <= 1 or not (investigation_summary or "").strip():
        return list(predictions)

    haystack = investigation_summary.lower()
    scores = [
        _root_cause_investigation_score(haystack, str(p.get("root_cause") or ""))
        for p in predictions
    ]
    rank1_score = scores[0]

    best_alt_idx: int | None = None
    best_alt_score = rank1_score
    for idx in range(1, len(predictions)):
        if scores[idx] > best_alt_score:
            best_alt_score = scores[idx]
            best_alt_idx = idx

    if best_alt_idx is None:
        return list(predictions)
    if best_alt_score <= rank1_score:
        return list(predictions)
    if best_alt_score < _MIN_PROMOTION_SCORE:
        return list(predictions)

    promoted = predictions[best_alt_idx]
    logger.info(
        "[investigation_handoff] promoting rank %d → 1: root_cause=%r "
        "(investigation score %d vs rank-1 score %d)",
        best_alt_idx + 1,
        promoted.get("root_cause"),
        best_alt_score,
        rank1_score,
    )

    new_order = [promoted, predictions[0]]
    for idx, prediction in enumerate(predictions):
        if idx in (0, best_alt_idx):
            continue
        new_order.append(prediction)

    return [
        {
            **prediction,
            "rank": new_rank + 1,
            "fault_taxonomy": _taxonomy_for_root_cause(
                str(prediction.get("root_cause") or "")
            ),
        }
        for new_rank, prediction in enumerate(new_order)
    ]


def apply_investigation_handoff(
    predictions: list[dict[str, Any]],
    investigation_summary: str,
) -> list[dict[str, Any]]:
    """Run B1 alignment then conservative evidence rerank (opensre path only).

    Order matters: B1 promotes when rank-2 is better supported than rank-1
    even if rank-1 has partial hits; conservative rerank then rescues the
    remaining "rank-1 never mentioned" cases.
    """
    if not (investigation_summary or "").strip():
        return list(predictions)

    from tests.benchmarks.cloudopsbench.predictor.rerank import rerank_predictions_by_evidence

    aligned = align_predictions_to_investigation(predictions, investigation_summary)
    return rerank_predictions_by_evidence(aligned, investigation_summary)
