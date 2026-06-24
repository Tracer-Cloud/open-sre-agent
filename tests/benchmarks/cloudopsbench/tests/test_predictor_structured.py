"""Tests for the OpenAI structured-outputs predictor variant.

These tests pin the anti-overfit invariants that the structured-outputs
mechanism depends on. If any test here fails, the schema has drifted from
the vocabulary or a corpus-specific addition slipped into the prompt
construction path — both of which would compromise the bench result's
honesty.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

import pytest
from pydantic import ValidationError

from tests.benchmarks.cloudopsbench.predictor.llm_call_structured_openai import (
    _Prediction,
    _PredictionsResponse,
    emit_paper_predictions_structured,
)
from tests.benchmarks.cloudopsbench.predictor.vocabulary import (
    _ROOT_CAUSES,
    _TAXONOMY_CATEGORIES,
)

# --------------------------------------------------------------------------- #
# Anti-overfit invariants — schema MUST be vocabulary-derived                 #
# --------------------------------------------------------------------------- #


def test_schema_root_cause_enum_matches_vocabulary() -> None:
    """The structured-output schema's ``root_cause`` enum must equal the
    scorer's vocabulary. Drift = corpus-specific value silently added in
    one place but not the other = overfit risk."""
    schema = _PredictionsResponse.model_json_schema()
    prediction_schema = schema["$defs"]["_Prediction"]
    schema_root_causes = tuple(prediction_schema["properties"]["root_cause"]["enum"])
    assert schema_root_causes == _ROOT_CAUSES, (
        "root_cause enum in structured-output schema diverged from "
        "vocabulary._ROOT_CAUSES. Single source of truth violated — both "
        "the scorer and the schema must read from the same tuple."
    )


def test_schema_fault_taxonomy_enum_matches_vocabulary() -> None:
    """Same invariant for ``fault_taxonomy``."""
    schema = _PredictionsResponse.model_json_schema()
    prediction_schema = schema["$defs"]["_Prediction"]
    schema_taxonomies = tuple(prediction_schema["properties"]["fault_taxonomy"]["enum"])
    assert schema_taxonomies == _TAXONOMY_CATEGORIES, (
        "fault_taxonomy enum in structured-output schema diverged from "
        "vocabulary._TAXONOMY_CATEGORIES. Same source of truth violation as "
        "above."
    )


def test_schema_rejects_off_vocab_root_cause() -> None:
    """Pydantic must refuse an off-vocabulary ``root_cause`` value.

    This is the layer above the OpenAI API enforcement — defense in depth.
    If somehow an off-vocab value reaches Pydantic (e.g., the OpenAI grammar
    layer is bypassed in a test or in a future provider port), Pydantic
    catches it.
    """
    with pytest.raises(ValidationError):
        _Prediction(
            rank=1,
            fault_taxonomy="Runtime_Fault",
            fault_object="app/some-service",
            root_cause="not_a_real_root_cause_token",  # noqa - intentional invalid
        )


def test_schema_rejects_off_vocab_fault_taxonomy() -> None:
    """Same invariant for fault_taxonomy."""
    with pytest.raises(ValidationError):
        _Prediction(
            rank=1,
            fault_taxonomy="Made_Up_Fault",  # noqa - intentional invalid
            fault_object="app/some-service",
            root_cause="mysql_invalid_port",
        )


def test_schema_accepts_fault_object_as_open_string() -> None:
    """``fault_object`` is intentionally kept as ``str`` (not Literal) so the
    bench reports impossible objects loudly rather than silently snapping to
    an in-set wrong one."""
    pred = _Prediction(
        rank=1,
        fault_taxonomy="Runtime_Fault",
        fault_object="app/some-novel-service-not-in-vocab",
        root_cause="mysql_invalid_port",
    )
    assert pred.fault_object == "app/some-novel-service-not-in-vocab"


def test_schema_enforces_rank_1_to_3() -> None:
    """rank must be a 1-3 integer. The scorer rejects anything else."""
    with pytest.raises(ValidationError):
        _Prediction(
            rank=0,
            fault_taxonomy="Runtime_Fault",
            fault_object="app/foo",
            root_cause="mysql_invalid_port",
        )
    with pytest.raises(ValidationError):
        _Prediction(
            rank=4,
            fault_taxonomy="Runtime_Fault",
            fault_object="app/foo",
            root_cause="mysql_invalid_port",
        )


def test_predictions_response_requires_at_least_one_and_at_most_three() -> None:
    """The list cardinality must be 1-3."""
    valid_pred = _Prediction(
        rank=1,
        fault_taxonomy="Runtime_Fault",
        fault_object="app/foo",
        root_cause="mysql_invalid_port",
    )
    with pytest.raises(ValidationError):
        _PredictionsResponse(top_3_predictions=[])
    with pytest.raises(ValidationError):
        _PredictionsResponse(top_3_predictions=[valid_pred] * 4)


# --------------------------------------------------------------------------- #
# Dispatch + happy-path behavior                                              #
# --------------------------------------------------------------------------- #


def _make_fake_openai_client(parsed_predictions: list[dict[str, Any]]) -> Any:
    """Build a fake OpenAI client that returns ``parsed_predictions`` from
    the structured-output call. The fake mirrors the real client's path:
    ``client.beta.chat.completions.parse(...).choices[0].message.parsed``.
    """
    fake_response = MagicMock()
    fake_response.top_3_predictions = [_Prediction(**p) for p in parsed_predictions]

    fake_completion = MagicMock()
    fake_completion.choices[0].message.parsed = fake_response
    fake_completion.usage.prompt_tokens = 100
    fake_completion.usage.completion_tokens = 50

    fake_client = MagicMock()
    fake_client.beta.chat.completions.parse.return_value = fake_completion
    return fake_client


def test_happy_path_returns_payload_shape() -> None:
    """End-to-end: fake OpenAI client → structured predictor → paper-format payload.

    Verifies the payload has the shape the scorer expects (top_3_predictions
    list with rank, fault_taxonomy, fault_object, root_cause keys).
    """
    fake_client = _make_fake_openai_client(
        [
            {
                "rank": 1,
                "fault_taxonomy": "Runtime_Fault",
                "fault_object": "app/checkoutservice",
                "root_cause": "liveness_probe_incorrect_port",
            },
            {
                "rank": 2,
                "fault_taxonomy": "Runtime_Fault",
                "fault_object": "app/checkoutservice",
                "root_cause": "liveness_probe_incorrect_timing",
            },
            {
                "rank": 3,
                "fault_taxonomy": "Runtime_Fault",
                "fault_object": "app/checkoutservice",
                "root_cause": "oom_killed",
            },
        ]
    )
    payload = emit_paper_predictions_structured(
        alert_text="checkoutservice is in CrashLoopBackOff",
        investigation_summary="liveness probe fails on port 5051",
        client=fake_client,
        model="gpt-4o-2024-11-20",
    )
    assert payload is not None
    assert "top_3_predictions" in payload
    assert len(payload["top_3_predictions"]) == 3
    for p in payload["top_3_predictions"]:
        assert set(p.keys()) == {"rank", "fault_taxonomy", "fault_object", "root_cause"}


def test_returns_none_when_api_raises() -> None:
    """API exceptions are caught and result in None — the scorer falls back."""
    fake_client = MagicMock()
    fake_client.beta.chat.completions.parse.side_effect = RuntimeError("simulated outage")
    payload = emit_paper_predictions_structured(
        alert_text="anything",
        investigation_summary="anything",
        client=fake_client,
        model="gpt-4o-2024-11-20",
    )
    assert payload is None


def test_returns_none_when_parsed_is_none() -> None:
    """A successful API call with an unparseable response → None."""
    fake_completion = MagicMock()
    fake_completion.choices[0].message.parsed = None
    fake_client = MagicMock()
    fake_client.beta.chat.completions.parse.return_value = fake_completion
    payload = emit_paper_predictions_structured(
        alert_text="anything",
        investigation_summary="anything",
        client=fake_client,
        model="gpt-4o-2024-11-20",
    )
    assert payload is None


def test_fault_taxonomy_is_derived_from_root_cause() -> None:
    """The schema constrains fault_taxonomy, but the final payload overrides
    it to the scorer's canonical mapping from root_cause.

    Reason: taxonomy is a function OF root_cause, not an independent dim.
    The LLM occasionally picks a surface-phase taxonomy for a root-cause
    family that belongs elsewhere; the override prevents that from costing
    A@1.
    """
    fake_client = _make_fake_openai_client(
        [
            {
                # LLM says Startup_Fault but mysql_invalid_credentials is Runtime
                "rank": 1,
                "fault_taxonomy": "Startup_Fault",
                "fault_object": "app/ts-auth-service",
                "root_cause": "mysql_invalid_credentials",
            }
        ]
    )
    payload = emit_paper_predictions_structured(
        alert_text="x",
        investigation_summary="x",
        client=fake_client,
        model="gpt-4o-2024-11-20",
    )
    assert payload is not None
    assert payload["top_3_predictions"][0]["fault_taxonomy"] == "Runtime_Fault"


# --------------------------------------------------------------------------- #
# Anti-overfit invariant: prompts are shared with the text predictor.        #
# Any prompt-side signal lives in llm_call.py, not duplicated/diverged here. #
# --------------------------------------------------------------------------- #


def test_structured_variant_uses_same_prompts_as_text_variant() -> None:
    """The structured variant must share ``_build_system_prompt`` and
    ``_build_user_prompt`` with the text variant — no divergent prompts.

    This is a static invariant: if someone copy-pastes the prompt
    construction into the structured file, drift between the two variants
    can introduce silent corpus signal. The shared imports prevent that.
    """
    from tests.benchmarks.cloudopsbench.predictor import (
        llm_call,
        llm_call_structured_openai,
    )

    # Both modules import the same prompt builders — verify they reference
    # the same callable object, not a copy.
    assert llm_call_structured_openai._build_system_prompt is llm_call._build_system_prompt
    assert llm_call_structured_openai._build_user_prompt is llm_call._build_user_prompt
