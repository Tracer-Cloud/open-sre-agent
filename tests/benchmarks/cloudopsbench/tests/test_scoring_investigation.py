"""Tests for investigation-native scoring + translation-loss metric.

These metrics answer "is opensre's INVESTIGATION getting better, or just
the LLM predictor that wraps its prose?" The headline ``a1`` score is
computed on ``top_3_predictions[0]`` — that's a second LLM call's
output. A lift on ``a1`` could come from opensre, the predictor, or the
handoff layer (B1/snap/rerank). Without a separate score on opensre's
text we can't tell.

``investigation_a1`` scores the keyword-parsed triple from opensre's
prose alone (``report`` + ``root_cause`` + ``final_state`` evidence —
deliberately NOT the predictor's ``final_answer`` field, which is the
predictor's output and would contaminate the metric).

``translation_loss`` flags cases where investigation_a1 is right but
``a1`` is wrong — opensre named the answer, the predictor lost it.

The keyword parser is conservative (misses synonyms), so
``investigation_a1`` is a lower bound on investigation quality, not the
truth. Read it as "≥X% of cases opensre's text named the GT triple."
"""

from __future__ import annotations

from tests.benchmarks.cloudopsbench.scoring import (
    _infer_fault_object,
    _score_investigation_native,
    infer_final_answer_from_opensre_text,
)

# Use service_dns_resolution_failure — the keyword parser needs only
# ("dns", "resolution", "no such host") + a known service name, which is
# achievable in realistic prose. The cartservice + env_var_address_mismatch
# rule requires SIX tokens at once and is hard to hit cleanly in tests.
_GT = {
    "fault_taxonomy": "Service_Routing_Fault",
    "fault_object": "app/frontend",
    "root_cause": "service_dns_resolution_failure",
}


def _case_data(
    *,
    report: str = "",
    root_cause: str = "",
    final_answer: object = None,
) -> dict:
    """Minimal case_data shape: only what the scorer reads."""
    data: dict = {}
    if report:
        data["report"] = report
    if root_cause:
        data["root_cause"] = root_cause
    if final_answer is not None:
        data["final_answer"] = final_answer
    return data


def test_investigation_a1_fires_when_opensre_prose_names_gt_triple() -> None:
    """opensre's report names the GT service + root cause → investigation_a1 = 1."""
    case_data = _case_data(
        report=(
            "Identified component: frontend.\n"
            "Investigation conclusion (root cause): frontend has a DNS "
            "resolution failure — no such host when calling upstream services."
        )
    )
    scores = _score_investigation_native(case_data, _GT)
    assert scores["a1"] == 1.0
    assert scores["object_a1"] == 1.0
    assert scores["partial_a1"] == 1.0


def test_investigation_a1_zero_when_prose_empty() -> None:
    """No opensre text → no signal → zero, not crash."""
    scores = _score_investigation_native({}, _GT)
    assert scores == {"a1": 0.0, "partial_a1": 0.0, "object_a1": 0.0}


def test_investigation_a1_does_not_read_predictor_final_answer() -> None:
    """Critical contamination guard: ``case_data["final_answer"]`` is the
    predictor's structured JSON. Stringifying it back through the keyword
    parser would feed predictor signal into the investigation metric and
    defeat the separation. The scorer MUST ignore that field.

    To make the test meaningful (rather than passing accidentally because
    the predictor dict happens to lack required tokens), use a rich-text
    ``final_answer`` that DOES contain all the keyword tokens — proving
    the contamination guard, not just the parser's natural ceiling."""
    case_data = _case_data(
        report="Could not determine a clear root cause.",
        # Rich text masquerading as predictor output: contains every token
        # needed to match service_dns_resolution_failure on the frontend.
        # If the scorer reads this, investigation_a1 incorrectly fires.
        final_answer=(
            "frontend has a DNS resolution failure — no such host found "
            "for upstream services. Localized to frontend."
        ),
    )
    scores = _score_investigation_native(case_data, _GT)
    assert scores["a1"] == 0.0
    assert scores["object_a1"] == 0.0


def test_infer_final_answer_default_reads_final_answer_text() -> None:
    """Backward-compat: existing callers (legacy keyword fallback in
    ``extract_final_answer_payload``) rely on the default behavior reading
    ``final_answer``. The flag must default to True. We use rich-text
    ``final_answer`` (matches the legacy pre-JSON-predictor era when the
    field carried free-form English) so the parser has actual tokens to
    match — proving the field IS read, not bypassed."""
    case_data = _case_data(
        final_answer=(
            "frontend has a DNS resolution failure — no such host found for upstream services."
        )
    )
    payload = infer_final_answer_from_opensre_text(case_data)
    assert payload is not None
    assert payload["top_3_predictions"][0]["fault_object"] == "app/frontend"


def test_infer_final_answer_excludes_final_answer_when_flag_off() -> None:
    """Flag off → ``final_answer`` field is NOT read into the parser text.
    Mirror of the contamination guard at the parser level. With the same
    rich-text final_answer as the default-behavior test, flag=False must
    flip the result from "extracted triple" to None."""
    case_data = _case_data(
        final_answer=(
            "frontend has a DNS resolution failure — no such host found for upstream services."
        )
    )
    payload = infer_final_answer_from_opensre_text(case_data, include_predictor_output=False)
    assert payload is None


def test_infer_fault_object_uses_full_train_ticket_vocabulary() -> None:
    """investigation_a1 depends on substring service match — tsdb-mysql must resolve."""
    assert _infer_fault_object("logs from tsdb-mysql show access denied") == "app/tsdb-mysql"
    # Longest-name-first: order-other beats order
    text = "fault localized to ts-order-other-service not ts-order-service"
    assert _infer_fault_object(text) == "app/ts-order-other-service"


def test_infer_fault_object_namespace_requires_anchor_word() -> None:
    """Precision guard: namespace match requires the literal word ``namespace``
    in the text. Without it, prose like "boutique system has memory pressure"
    would falsely return ``namespace/boutique`` and inflate investigation_a1
    on the small fraction of cases whose GT is actually a ``namespace/<X>``
    fault, while shifting investigation outputs on every case whose GT is a
    service or node. Regression guard for the 2026-06 vocab-import refactor
    that dropped this anchor."""
    # Casual cluster-name mention WITHOUT the word "namespace" → empty.
    # No service / node match → must NOT fall through to namespace match.
    assert _infer_fault_object("boutique system has memory pressure") == ""
    assert _infer_fault_object("train-ticket cluster was slow today") == ""
    # Explicit namespace localization DOES match — the guard is the literal
    # word "namespace", not a stricter grammar.
    assert _infer_fault_object("issue localized to the boutique namespace") == "namespace/boutique"
    assert (
        _infer_fault_object("the train-ticket namespace was quota-throttled")
        == "namespace/train-ticket"
    )
    # Service match still wins even when both "namespace" + cluster name
    # appear — the service loop runs first.
    text = "cartservice failure in the boutique namespace"
    assert _infer_fault_object(text) == "app/cartservice"


def test_investigation_a1_zero_when_root_cause_phrasing_does_not_match() -> None:
    """Parser sees the service name but the root cause phrasing doesn't match
    any keyword set → all zeros, because ``infer_final_answer_from_opensre_text``
    requires BOTH a root_cause AND a fault_object to emit a triple. Pins the
    conservative-floor contract: investigation_a1 will undercount opensre's
    real quality, and we report it as a lower bound."""
    case_data = _case_data(
        report=("frontend was experiencing some configuration issue but I'm not sure exactly what.")
    )
    scores = _score_investigation_native(case_data, _GT)
    assert scores["a1"] == 0.0
    assert scores["object_a1"] == 0.0
