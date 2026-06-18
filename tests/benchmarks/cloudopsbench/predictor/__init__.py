"""Paper-format ``top_3_predictions`` predictor — package split.

Originally a single ``predictor.py`` file; split on 2026-06-09 into four
focused modules so the upcoming structured-outputs experiment can land in
``llm_call_structured.py`` without bloating an already-overloaded file.

Module layout:
  - ``vocabulary.py`` — closed-vocabulary constants (taxonomies, root_causes,
    fault_object services / nodes / namespaces). Single source of truth for
    the scorer enum surfaces and the structured-output schema enums.
  - ``snapping.py`` — Lever A: controlled-vocabulary snapping with the
    cross-concept blocklist guard.
  - ``rerank.py`` — Lever D: conservative evidence-weighted top-3 rescue.
  - ``llm_call.py`` — the default text-emit predictor LLM call + prompt
    construction + response parsing.
  - ``llm_call_structured_openai.py`` — OpenAI structured-outputs predictor
    variant. Same prompts as ``llm_call.py``, but grammar-constrained
    sampling at the API layer via ``response_format`` + Pydantic Literal
    enums from ``vocabulary.py``. Selected via
    ``predictor_variant: "structured"`` in the bench config. Future
    multi-provider peers: ``llm_call_structured_anthropic.py``,
    ``llm_call_structured_deepseek.py``.

Backward-compat re-exports: existing
``from tests.benchmarks.cloudopsbench.predictor import X`` callers keep
working because every public (and underscore-private) name from the four
modules above is re-exported here.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from tests.benchmarks.cloudopsbench.predictor.llm_call import (
    _FENCED_JSON,
    _build_system_prompt,
    _build_user_prompt,
    _parse_predictions,
    emit_paper_predictions,
)
from tests.benchmarks.cloudopsbench.predictor.llm_call_structured_openai import (
    emit_paper_predictions_structured,
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

if TYPE_CHECKING:
    # ``align_predictions_to_investigation`` and ``apply_investigation_handoff`` are
    # PEP 562 lazy module attributes resolved by ``__getattr__`` below (see the
    # docstring there). Declaring them here makes them visible to static analyzers
    # that can't follow ``__getattr__`` (CodeQL ``py/undefined-export``, ruff F822)
    # without eagerly importing ``investigation_handoff`` and its scoring deps.
    from tests.benchmarks.cloudopsbench.predictor.investigation_handoff import (
        align_predictions_to_investigation,
        apply_investigation_handoff,
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
    # investigation handoff (B1)
    "align_predictions_to_investigation",
    "apply_investigation_handoff",
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
    # llm_call_structured_openai
    "emit_paper_predictions_structured",
]

_INVESTIGATION_HANDOFF_EXPORTS = frozenset(
    {"align_predictions_to_investigation", "apply_investigation_handoff"}
)


def __getattr__(name: str) -> Any:
    """Lazy-load investigation handoff so importing ``vocabulary`` from this
    package does not pull in handoff (and its scoring dependencies) at init."""
    if name in _INVESTIGATION_HANDOFF_EXPORTS:
        from tests.benchmarks.cloudopsbench.predictor.investigation_handoff import (
            align_predictions_to_investigation,
            apply_investigation_handoff,
        )

        return {
            "align_predictions_to_investigation": align_predictions_to_investigation,
            "apply_investigation_handoff": apply_investigation_handoff,
        }[name]
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
