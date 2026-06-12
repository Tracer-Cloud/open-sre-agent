"""OpenAI structured-outputs variant of the predictor.

Targets the residual predictor drift (OBJECT_HIT_RC_MISS) measured at 24%
of Runtime losses + 8.8% of all opensre+llm cells on the post-vocab-fix
baseline. The mechanism is API-layer schema enforcement: the LLM literally
cannot emit out-of-enum tokens for ``root_cause`` and ``fault_taxonomy``.

Multi-provider plan (this file = the OpenAI implementation):
  - **OpenAI** (this module): ``client.beta.chat.completions.parse()`` with
    a Pydantic schema whose Literal enums come from ``vocabulary.py``.
    Requires ``gpt-4o-2024-08-06+`` or ``gpt-5``.
  - **Anthropic** (planned, ``llm_call_structured_anthropic.py``):
    tool-use with ``input_schema`` enums + ``tool_choice: {type: "tool",
    name: "..."}`` to force structured emit. Works on Claude 3+ models.
  - **DeepSeek** (planned, ``llm_call_structured_deepseek.py``):
    OpenAI-compatible ``response_format`` — likely a thin wrapper around
    the OpenAI client pointed at DeepSeek's base URL.

The mechanism is the same across providers (grammar-constrained sampling);
the API shapes differ. The dispatcher in ``adapter.py`` routes to the
correct per-provider variant based on the configured LLM.

Anti-overfit discipline:
  - The Pydantic schema's enum surfaces are built **programmatically** from
    ``vocabulary.py`` — same source of truth the scorer's enums and the
    text predictor's system prompt read from. There is no place in this
    module to silently add a corpus-specific token; any addition has to
    land in ``vocabulary.py`` where the scorer also sees it.
  - The system + user prompts are reused verbatim from the text predictor.
    No prompt-side signal is added that names corpus services or fault
    patterns. The lift, if any, comes from grammar-constrained sampling
    alone — not from learning the corpus.
  - ``fault_object`` is kept as ``str`` (not Literal). It would be
    over-constrained to enumerate every known service/node/namespace
    because the scorer accepts open ``namespace_<reason>`` tokens and we
    want the schema to fail loudly on impossible objects, not silently
    snap to an in-set wrong one.
  - Snapping is still applied to ``fault_object`` (canonical-form
    normalization) but is now a no-op on ``root_cause`` because the
    schema guarantees in-vocab values.

OpenAI-only feature. ``client.beta.chat.completions.parse()`` requires
``gpt-4o-2024-08-06+`` or any ``gpt-5`` model. For Anthropic / non-OpenAI
predictors, callers fall back to the default text-emit variant in
``llm_call.py``.

Why bench-only: this is the predictor that formalizes opensre's
investigation into paper-format JSON for scoring. Schema enforcement here
makes the bench number more honest (no off-vocab fallout) but is not part
of opensre's production investigation behavior. Production opensre is
untouched.
"""

from __future__ import annotations

import logging
import os
from typing import Any, Literal

from openai import OpenAI
from pydantic import BaseModel, ConfigDict, Field

from app.services.llm_client import _emit_usage  # noqa: PLC2701 — bench needs cost tracking
from app.utils.llm_retry import LLMCreditExhaustedError, retry_on_rate_limit
from tests.benchmarks.cloudopsbench.predictor.llm_call import (
    _build_system_prompt,
    _build_user_prompt,
)
from tests.benchmarks.cloudopsbench.predictor.snapping import _snap_fault_object
from tests.benchmarks.cloudopsbench.predictor.vocabulary import (
    _ROOT_CAUSES,
    _TAXONOMY_CATEGORIES,
)
from tests.benchmarks.cloudopsbench.taxonomy import taxonomy_for_root_cause

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────────────────────
# Schema — programmatic Literal types from the closed vocabulary
# ─────────────────────────────────────────────────────────────────────────────
# These MUST stay tied to ``vocabulary.py``. Tests in test_predictor_structured.py
# assert equality so any drift between schema enum and scorer enum fails
# loudly in CI rather than silently undermining the bench number.

_TaxonomyLit = Literal[*_TAXONOMY_CATEGORIES]  # type: ignore[valid-type]
_RootCauseLit = Literal[*_ROOT_CAUSES]  # type: ignore[valid-type]


class _Prediction(BaseModel):
    """One entry of the top_3_predictions list, constrained by enum schema.

    ``fault_taxonomy`` and ``root_cause`` are enum-constrained — the LLM
    cannot emit out-of-vocab tokens. ``fault_object`` is left as ``str``
    because the canonical set is open (services + nodes + namespaces +
    open ``namespace_<reason>`` family) and we want the bench to detect
    impossible objects rather than silently snap to wrong in-set ones.
    """

    model_config = ConfigDict(extra="forbid")

    rank: int = Field(ge=1, le=3)
    fault_taxonomy: _TaxonomyLit
    fault_object: str
    root_cause: _RootCauseLit


class _PredictionsResponse(BaseModel):
    """Top-level OpenAI structured-output response shape."""

    model_config = ConfigDict(extra="forbid")

    top_3_predictions: list[_Prediction] = Field(min_length=1, max_length=3)


# ─────────────────────────────────────────────────────────────────────────────
# Public API — mirrors emit_paper_predictions for dispatch compatibility
# ─────────────────────────────────────────────────────────────────────────────


def emit_paper_predictions_structured(
    *,
    alert_text: str,
    investigation_summary: str,
    metric_alerts: str = "",
    performance_localization_hint: dict[str, str] | None = None,
    client: OpenAI | None = None,
    model: str | None = None,
) -> dict[str, Any] | None:
    """Ask the LLM to emit paper-format predictions via OpenAI structured outputs.

    Returns the parsed payload ``{"top_3_predictions": [...]}`` on success,
    or ``None`` on any failure (parse, network, schema mismatch). On
    ``None``, the existing scorer fallback runs — same no-regression
    contract as the text predictor.

    Cost: emits ``_emit_usage`` after a successful call so the bench
    runner's CostTracker hook (registered via ``set_usage_hook``) sees
    structured-output token spend in the aggregate cost number.

    Test injection: ``client`` and ``model`` are exposed so tests can
    pass a fake OpenAI client without env vars. Production callers omit
    both — ``client`` defaults to a fresh ``OpenAI()`` (reads
    ``OPENAI_API_KEY``), ``model`` defaults to the bench-pinned model
    via ``OPENSRE_BENCH_PREDICTOR_MODEL`` env var or
    ``gpt-4o-2024-11-20`` (the same default used in the text predictor
    on the bench harness).

    Note: this variant takes no ``llm`` parameter because structured
    outputs is OpenAI-specific. The dispatcher in ``adapter.py`` is
    responsible for choosing this variant only when the configured model
    supports it.
    """
    resolved_model = model or os.environ.get("OPENSRE_BENCH_PREDICTOR_MODEL", "gpt-4o-2024-11-20")
    resolved_client = client or OpenAI()

    system = _build_system_prompt()
    user_content = _build_user_prompt(
        alert_text,
        investigation_summary,
        metric_alerts=metric_alerts,
        performance_localization_hint=performance_localization_hint,
    )

    try:
        # Intentionally NOT passing `seed=` to the OpenAI API. Fixing the seed
        # at this layer makes every replicate-run identical, which (a) defeats
        # ``runs_per_case`` (the three runs collapse to one), and (b) makes the
        # A/A consistency guard (``seed_pair: [42, 43]``) report a trivial 0
        # diff regardless of the bench's true variance floor. The text
        # predictor in ``llm_call.py`` also omits ``seed`` for the same
        # reason. Structural reproducibility comes from pinned model version
        # + dataset SHA + framework + vocabulary, not from API-level seeding.
        completion = retry_on_rate_limit(
            lambda: resolved_client.beta.chat.completions.parse(
                model=resolved_model,
                messages=[
                    {"role": "system", "content": system},
                    {"role": "user", "content": user_content},
                ],
                response_format=_PredictionsResponse,
            ),
            label="predictor_structured",
        )
    except LLMCreditExhaustedError:
        raise
    except Exception as exc:  # noqa: BLE001 — best-effort step; never block scoring
        logger.warning("[predictor_structured] OpenAI structured-output call failed: %s", exc)
        return None

    parsed = completion.choices[0].message.parsed
    if parsed is None:
        logger.warning("[predictor_structured] structured-output parse returned None")
        return None

    # Emit cost — usage is on the chat completion's ``usage`` field.
    usage = getattr(completion, "usage", None)
    if usage is not None:
        _emit_usage(
            resolved_model,
            getattr(usage, "prompt_tokens", 0) or 0,
            getattr(usage, "completion_tokens", 0) or 0,
        )

    cleaned: list[dict[str, Any]] = []
    for prediction in parsed.top_3_predictions:
        # fault_taxonomy is overridden to the scorer's mapping (same policy as
        # the text predictor — taxonomy is a function OF root_cause). Even
        # though the schema constrains the LLM's emit, the scorer's mapping
        # is the canonical ground truth and may differ from what the LLM
        # picked for the same root_cause.
        derived_taxonomy = taxonomy_for_root_cause(prediction.root_cause)
        cleaned.append(
            {
                "rank": prediction.rank,
                "fault_taxonomy": derived_taxonomy,
                # ``root_cause`` snap is a no-op (schema guarantees in-vocab),
                # but we keep ``fault_object`` snapping because that field is
                # an open str type — could need canonical-form normalization.
                "fault_object": _snap_fault_object(prediction.fault_object),
                "root_cause": prediction.root_cause,
            }
        )

    return {"top_3_predictions": cleaned}
