"""Paper-format ``top_3_predictions`` predictor — package split.

Originally a single ``predictor.py`` file; split on 2026-06-09 into four
focused modules so the upcoming structured-outputs experiment can land in
``llm_call_structured.py`` without bloating an already-overloaded file.

Module layout:
  - ``vocabulary.py`` — closed-vocabulary constants (taxonomies, root_causes,
    fault_object services / nodes / namespaces). Single source of truth for
    the scorer enum surfaces.
  - ``snapping.py`` — Lever A: controlled-vocabulary snapping with the
    cross-concept blocklist guard.
  - ``rerank.py`` — Lever D: conservative evidence-weighted top-3 rescue.
  - ``llm_call.py`` — the predictor LLM call + prompt construction +
    response parsing.

Backward-compat re-exports: existing
``from tests.benchmarks.cloudopsbench.predictor import X`` callers keep
working because every public (and underscore-private) name from the four
modules above is re-exported here.
"""

from __future__ import annotations

from tests.benchmarks.cloudopsbench.predictor.llm_call import (
    _FENCED_JSON,
    _build_system_prompt,
    _build_user_prompt,
    _parse_predictions,
    emit_paper_predictions,
)
from tests.benchmarks.cloudopsbench.predictor.rerank import (
    _RERANK_MIN_TOKEN_LEN,
    _RERANK_STOPWORDS,
    _prediction_tokens,
    rerank_predictions_by_evidence,
)
from tests.benchmarks.cloudopsbench.predictor.snapping import (
    _BLOCKED_CONCEPT_PAIRS,
    _KNOWN_NAMESPACES_BY_NORM,
    _KNOWN_NODES_BY_NORM,
    _KNOWN_SERVICES_BY_NORM,
    _ROOT_CAUSE_BY_NORM,
    _ROOT_CAUSE_SNAP_CUTOFF,
    _crosses_blocked_concept_boundary,
    _snap_fault_object,
    _snap_root_cause,
)
from tests.benchmarks.cloudopsbench.predictor.vocabulary import (
    _FAULT_OBJECT_NAMESPACES,
    _FAULT_OBJECT_NODES,
    _FAULT_OBJECT_SERVICES,
    _ROOT_CAUSES,
    _TAXONOMY_CATEGORIES,
)

__all__ = [
    # vocabulary
    "_FAULT_OBJECT_NAMESPACES",
    "_FAULT_OBJECT_NODES",
    "_FAULT_OBJECT_SERVICES",
    "_ROOT_CAUSES",
    "_TAXONOMY_CATEGORIES",
    # snapping
    "_BLOCKED_CONCEPT_PAIRS",
    "_KNOWN_NAMESPACES_BY_NORM",
    "_KNOWN_NODES_BY_NORM",
    "_KNOWN_SERVICES_BY_NORM",
    "_ROOT_CAUSE_BY_NORM",
    "_ROOT_CAUSE_SNAP_CUTOFF",
    "_crosses_blocked_concept_boundary",
    "_snap_fault_object",
    "_snap_root_cause",
    # rerank
    "_RERANK_MIN_TOKEN_LEN",
    "_RERANK_STOPWORDS",
    "_prediction_tokens",
    "rerank_predictions_by_evidence",
    # llm_call
    "_FENCED_JSON",
    "_build_system_prompt",
    "_build_user_prompt",
    "_parse_predictions",
    "emit_paper_predictions",
]
