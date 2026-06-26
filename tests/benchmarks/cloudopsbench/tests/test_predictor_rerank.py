"""Tests for the evidence-weighted top-3 re-ranker (Lever D).

11:46 failure analysis showed 53% of failures had the correct fault_object
SOMEWHERE in top-3 but only 38% at rank-1 — 15 points of object accuracy
sitting in ranks 2-3 because the LLM's confidence ordering didn't put the
best-evidenced candidate first. The re-ranker resolves this by scoring
each prediction against the actual investigation evidence and reordering
DESC by citation count, stable on ties.

These tests pin the contract:

  - identity on degenerate inputs (≤1 pred, blank evidence, all-tied scores)
  - rank-2 with more evidence-substring hits gets promoted to rank-1
  - the ``rank`` field on each dict is rewritten so position and rank agree
  - structural stop-words (`service`, `app`, etc.) are NOT counted —
    otherwise every K8s diagnosis ties at the same noise floor
  - input list is NEVER mutated (returns a new list)
"""

from __future__ import annotations

from typing import Any

import pytest

from tests.benchmarks.cloudopsbench.predictor import (
    _prediction_tokens,
    rerank_predictions_by_evidence,
)


def _pred(
    rank: int, fault_object: str, root_cause: str, taxonomy: str = "Runtime_Fault"
) -> dict[str, Any]:
    return {
        "rank": rank,
        "fault_taxonomy": taxonomy,
        "fault_object": fault_object,
        "root_cause": root_cause,
    }


# --------------------------------------------------------------------------- #
# Identity short-circuits                                                     #
# --------------------------------------------------------------------------- #


def test_empty_predictions_returns_empty() -> None:
    """Defensive: a predictor failure (no payload) produces an empty list.
    Reranker must not crash and must not invent predictions."""
    assert rerank_predictions_by_evidence([], "any evidence text") == []


def test_single_prediction_returns_unchanged() -> None:
    """1 element can't be reordered — return it as-is. Guards against an
    accidental rank-rewrite that would conflict with the input rank."""
    pred = _pred(1, "app/paymentservice", "liveness_probe_incorrect_port")
    result = rerank_predictions_by_evidence([pred], "evidence")
    assert result == [pred]


def test_blank_evidence_preserves_order() -> None:
    """No evidence text means no signal to discriminate on. Order MUST be
    identity (rank-1 stays rank-1) so a re-rank pass never silently
    degrades a run that produced no investigation summary."""
    preds = [
        _pred(1, "app/svc-a", "cause_one"),
        _pred(2, "app/svc-b", "cause_two"),
        _pred(3, "app/svc-c", "cause_three"),
    ]
    assert rerank_predictions_by_evidence(preds, "") == preds
    assert rerank_predictions_by_evidence(preds, "   \n   ") == preds


def test_all_predictions_tie_on_score_preserves_order() -> None:
    """When every prediction's tokens score equal hits, the stable sort
    must preserve input order so the LLM's confidence ordering survives
    the no-op case. Critical for ensuring re-rank is monotonic."""
    preds = [
        _pred(1, "app/cartservice", "memory_leak"),
        _pred(2, "app/checkoutservice", "memory_leak"),
        _pred(3, "app/paymentservice", "memory_leak"),
    ]
    # Evidence mentions all three services equally → all score = same
    evidence = "cartservice failure observed. checkoutservice failure observed. paymentservice failure observed. memory leak suspected."
    result = rerank_predictions_by_evidence(preds, evidence)
    assert [p["fault_object"] for p in result] == [
        "app/cartservice",
        "app/checkoutservice",
        "app/paymentservice",
    ]


# --------------------------------------------------------------------------- #
# Re-rank fires when evidence prefers a non-rank-1 candidate                   #
# --------------------------------------------------------------------------- #


def test_rank_1_with_zero_evidence_is_rescued_by_rank_2_with_hits() -> None:
    """The headline rescue case: the LLM's rank-1 prediction is NOT
    mentioned in the investigation at all (zero evidence hits), while a
    rank-2 prediction is heavily cited. The conservative ranker promotes
    the rank-2. This is the only condition under which the ranker fires —
    everything else is identity, by design (see the empirical regression
    documented in the function docstring)."""
    preds = [
        _pred(1, "app/unrelatedone", "unmentioned_cause"),  # 0 evidence hits
        _pred(2, "app/cartservice", "redis_connection_refused"),  # 3+ evidence hits
        _pred(3, "app/emailservice", "smtp_timeout"),
    ]
    evidence = (
        "cartservice repeatedly logs redis connection refused. "
        "cartservice pods restart every 30s. checked cartservice "
        "configuration. redis password matches."
    )
    result = rerank_predictions_by_evidence(preds, evidence)
    assert result[0]["fault_object"] == "app/cartservice"
    assert result[0]["rank"] == 1


def test_rank_1_with_any_hit_is_NOT_overruled_even_if_rank_2_has_more() -> None:
    """**Critical safety invariant** — pinned against the empirical
    regression from the 11:46 replay. When the LLM's rank-1 has ANY
    evidence backing at all, defer to its confidence ordering — even if
    a rank-2 has MORE hits. Substring count is too noisy to over-rule a
    confidence-ranked-and-evidenced top pick; the permissive variant
    cost 7.2pp on A@1 in testing."""
    preds = [
        _pred(1, "app/paymentservice", "wrong_cause"),  # 1 evidence hit (paymentservice)
        _pred(2, "app/cartservice", "redis_connection_refused"),  # 3+ evidence hits
        _pred(3, "app/emailservice", "smtp_timeout"),
    ]
    evidence = (
        "cartservice repeatedly logs redis connection refused. "
        "cartservice pods restart every 30s. checked cartservice "
        "configuration. paymentservice appears healthy."  # paymentservice mentioned once
    )
    result = rerank_predictions_by_evidence(preds, evidence)
    # paymentservice stays at rank-1 despite cartservice having more hits
    assert result[0]["fault_object"] == "app/paymentservice"
    assert [p["fault_object"] for p in result] == [p["fault_object"] for p in preds]


def test_rank_3_with_dominant_evidence_can_jump_to_rank_1() -> None:
    """Rank-3 isn't a tiebreak ceiling — if it's the only prediction with
    matching evidence tokens, it wins rank-1. Pins the "no cap on
    promotion distance" behavior."""
    preds = [
        _pred(1, "app/wrongone", "irrelevant_cause"),
        _pred(2, "app/alsowrong", "another_red_herring"),
        _pred(3, "app/paymentservice", "mysql_invalid_credentials"),
    ]
    evidence = (
        "paymentservice fails with mysql access denied. "
        "paymentservice credentials in env secret. "
        "mysql user paymentservice rejected."
    )
    result = rerank_predictions_by_evidence(preds, evidence)
    assert result[0]["fault_object"] == "app/paymentservice"
    assert result[0]["rank"] == 1


# --------------------------------------------------------------------------- #
# rank field rewriting                                                        #
# --------------------------------------------------------------------------- #


def test_rank_field_rewritten_to_match_new_position() -> None:
    """After reranking, the dict's ``rank`` field must equal its new
    1-based position. Downstream JSON serialization relies on this — a
    serializer that emits ``predictions[0].rank == 2`` is confusing and
    masks the reranker's effect when reading raw artifacts."""
    preds = [
        _pred(1, "app/wrong", "x"),
        _pred(2, "app/correct", "right_cause"),
        _pred(3, "app/other", "y"),
    ]
    result = rerank_predictions_by_evidence(preds, "correct service had right_cause")
    assert [p["rank"] for p in result] == [1, 2, 3]


def test_other_prediction_fields_pass_through_unchanged() -> None:
    """Reranking only changes order + rank. Other fields (taxonomy,
    explanations the LLM emitted) must come through verbatim — losing
    them silently would hide diagnostic detail in the per-case JSON."""
    # Rescue case: rank-1 unmentioned, rank-2 cited → swap happens
    preds = [
        _pred(1, "app/unmentioned", "phantom_cause", taxonomy="Runtime_Fault"),
        _pred(2, "app/cartservice", "redis_connection_refused", taxonomy="Startup_Fault"),
    ]
    result = rerank_predictions_by_evidence(
        preds, "cartservice redis redis connection refused cartservice"
    )
    assert result[0]["fault_object"] == "app/cartservice"
    assert result[0]["fault_taxonomy"] == "Startup_Fault"
    assert result[1]["fault_taxonomy"] == "Runtime_Fault"


def test_input_predictions_not_mutated() -> None:
    """Pure function contract — the input list and its dicts must be
    untouched. Otherwise a caller holding a reference to the original
    payload sees their data mutate behind their back."""
    preds = [
        _pred(1, "app/wrong", "x"),
        _pred(2, "app/correct", "right_cause"),
    ]
    original_rank_1 = preds[0]["rank"]
    original_obj = preds[0]["fault_object"]
    _ = rerank_predictions_by_evidence(preds, "correct service right cause")
    assert preds[0]["rank"] == original_rank_1
    assert preds[0]["fault_object"] == original_obj


# --------------------------------------------------------------------------- #
# Stop-word & token-extraction discipline                                     #
# --------------------------------------------------------------------------- #


def test_prediction_tokens_drops_stop_words_and_short_tokens() -> None:
    """The fault_object prefix (`app`, `node`, `namespace`) and structural
    K8s nouns (`service`, `pod`) are stop-words — counting them inflates
    every K8s prediction equally, defeating the rerank. Single chars and
    2-letter tokens are dropped as too noisy to substring-match
    reliably."""
    tokens = _prediction_tokens(_pred(1, "app/paymentservice", "mysql_invalid_credentials"))
    # Stop-words gone
    assert "app" not in tokens
    assert "service" not in tokens
    # Substantive tokens retained
    assert "paymentservice" in tokens
    assert "mysql" in tokens
    assert "invalid" in tokens
    assert "credentials" in tokens


def test_prediction_tokens_lowercases_for_substring_match() -> None:
    """Evidence text is also lowercased before matching — token-side
    lowercasing must be in sync, else a case-sensitive miss costs accuracy."""
    tokens = _prediction_tokens(_pred(1, "App/PaymentService", "MYSQL_Invalid_Credentials"))
    assert all(t == t.lower() for t in tokens)


def test_stop_words_alone_dont_distinguish_two_predictions() -> None:
    """Regression-pin: if ``service`` (a stop-word) were the only token
    difference between two predictions, the rerank must NOT prefer one
    over the other based on it. Forces the discriminating signal to come
    from real entity names."""
    preds = [
        _pred(1, "app/svc-a", "generic_fault"),
        _pred(2, "app/svc-b", "generic_fault"),
    ]
    evidence = "service service service fault fault"  # only stop-words
    result = rerank_predictions_by_evidence(preds, evidence)
    # Order preserved — no signal to distinguish on
    assert [p["fault_object"] for p in result] == ["app/svc-a", "app/svc-b"]


# --------------------------------------------------------------------------- #
# fault_object prefix stripping                                                #
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    ("raw_object", "expected_token"),
    [
        ("app/paymentservice", "paymentservice"),
        ("node/worker-01", "worker"),  # "01" too short, dropped
        ("namespace/boutique", "boutique"),
        ("plain-name-no-prefix", "plain"),  # split on dashes
    ],
)
def test_prediction_tokens_strips_fault_object_prefix(raw_object: str, expected_token: str) -> None:
    """The prefix (``config/`` etc.) is metadata, not a discriminator —
    every K8s diagnosis has one. Drop it before tokenizing so the
    rerank counts the name itself, not the prefix shared by all
    predictions."""
    tokens = _prediction_tokens(_pred(1, raw_object, ""))
    assert expected_token in tokens
