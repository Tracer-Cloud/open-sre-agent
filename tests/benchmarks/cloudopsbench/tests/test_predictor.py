"""Tests for the paper-format prediction emitter.

Cover the response-parsing edge cases (bare JSON, fenced JSON, malformed
JSON, missing fields) and the end-to-end flow with a fake LLM, plus the
mode-agnostic shape (empty investigation_summary for llm_alone)."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from tests.benchmarks.cloudopsbench.predictor import (
    _parse_predictions,
    emit_paper_predictions,
)

# --------------------------------------------------------------------------- #
# Fake LLM client                                                             #
# --------------------------------------------------------------------------- #


@dataclass
class _FakeLLMResponse:
    content: str
    tool_calls: list[Any] = field(default_factory=list)


class _FakeLLM:
    """Returns a canned content string. Records the call for assertions."""

    def __init__(self, content: str, *, raise_on_invoke: bool = False) -> None:
        self._content = content
        self._raise = raise_on_invoke
        self.invoked_with: dict[str, Any] | None = None

    def invoke(self, messages: list[dict[str, Any]], system: str | None = None) -> _FakeLLMResponse:
        if self._raise:
            raise RuntimeError("LLM failure (simulated)")
        self.invoked_with = {"messages": messages, "system": system}
        return _FakeLLMResponse(content=self._content)


# --------------------------------------------------------------------------- #
# _parse_predictions edge cases                                               #
# --------------------------------------------------------------------------- #


def test_parse_predictions_accepts_bare_json() -> None:
    text = (
        '{"top_3_predictions": ['
        '{"rank": 1, "fault_taxonomy": "Runtime_Fault",'
        ' "fault_object": "app/ts-voucher-service",'
        ' "root_cause": "mysql_invalid_credentials"}'
        "]}"
    )
    parsed = _parse_predictions(text)
    assert parsed is not None
    assert len(parsed["top_3_predictions"]) == 1
    assert parsed["top_3_predictions"][0]["fault_object"] == "app/ts-voucher-service"


def test_parse_predictions_accepts_fenced_json() -> None:
    text = (
        "Here is the JSON:\n"
        "```json\n"
        '{"top_3_predictions": ['
        '{"rank": 1, "fault_taxonomy": "Startup_Fault",'
        ' "fault_object": "app/emailservice",'
        ' "root_cause": "image_registry_dns_failure"}'
        "]}\n"
        "```"
    )
    parsed = _parse_predictions(text)
    assert parsed is not None
    assert parsed["top_3_predictions"][0]["root_cause"] == "image_registry_dns_failure"


def test_parse_predictions_accepts_unlabeled_fence() -> None:
    text = (
        "```\n"
        '{"top_3_predictions": [{"rank": 1, "fault_taxonomy": "Runtime_Fault",'
        ' "fault_object": "app/frontend", "root_cause": "oom_killed"}]}\n'
        "```"
    )
    parsed = _parse_predictions(text)
    assert parsed is not None
    assert parsed["top_3_predictions"][0]["fault_taxonomy"] == "Runtime_Fault"


def test_parse_predictions_rejects_malformed_json() -> None:
    assert _parse_predictions("{not actually json") is None
    assert _parse_predictions("") is None


def test_parse_predictions_rejects_missing_top_3_predictions() -> None:
    assert _parse_predictions('{"something_else": []}') is None
    assert _parse_predictions('{"top_3_predictions": []}') is None


def test_parse_predictions_drops_entries_missing_required_fields() -> None:
    text = (
        '{"top_3_predictions": ['
        '{"rank": 1, "fault_object": "app/frontend"},'  # missing root_cause
        '{"rank": 2, "root_cause": "oom_killed"},'  # missing fault_object
        '{"rank": 3, "fault_taxonomy": "Runtime_Fault",'
        ' "fault_object": "app/checkoutservice", "root_cause": "deployment_zero_replicas"}'
        "]}"
    )
    parsed = _parse_predictions(text)
    assert parsed is not None
    # Only the 3rd entry has both required fields.
    assert len(parsed["top_3_predictions"]) == 1
    assert parsed["top_3_predictions"][0]["root_cause"] == "deployment_zero_replicas"


def test_parse_predictions_caps_at_three_entries() -> None:
    raw = ",".join(
        [
            f'{{"rank": {i}, "fault_taxonomy": "Runtime_Fault",'
            ' "fault_object": "app/frontend", "root_cause": "oom_killed"}'
            for i in range(1, 6)
        ]
    )
    text = f'{{"top_3_predictions": [{raw}]}}'
    parsed = _parse_predictions(text)
    assert parsed is not None
    assert len(parsed["top_3_predictions"]) == 3


# --------------------------------------------------------------------------- #
# emit_paper_predictions — end-to-end with fake LLM                           #
# --------------------------------------------------------------------------- #


def test_emit_paper_predictions_happy_path_with_opensre_summary() -> None:
    llm_output = (
        '{"top_3_predictions": ['
        '{"rank": 1, "fault_taxonomy": "Runtime_Fault",'
        ' "fault_object": "app/ts-voucher-service",'
        ' "root_cause": "mysql_invalid_credentials"}'
        "]}"
    )
    llm = _FakeLLM(llm_output)

    payload = emit_paper_predictions(
        alert_text="alert_name: trainticket/runtime/56",
        investigation_summary="ts-voucher-service Access denied for user 'ts'",
        llm=llm,
    )

    assert payload is not None
    assert payload["top_3_predictions"][0]["root_cause"] == "mysql_invalid_credentials"
    # System prompt must teach the paper schema.
    assert llm.invoked_with is not None
    assert "top_3_predictions" in (llm.invoked_with["system"] or "")
    assert "mysql_invalid_credentials" in (llm.invoked_with["system"] or "")
    # User message carries both alert and investigation summary.
    user_content = llm.invoked_with["messages"][0]["content"]
    assert "trainticket/runtime/56" in user_content
    assert "Access denied" in user_content


def test_emit_paper_predictions_llm_alone_path_passes_alert_only() -> None:
    """llm_alone mode passes empty investigation_summary; predictor still works."""
    llm = _FakeLLM(
        '{"top_3_predictions": ['
        '{"rank": 1, "fault_taxonomy": "Startup_Fault",'
        ' "fault_object": "app/emailservice",'
        ' "root_cause": "image_registry_dns_failure"}'
        "]}"
    )

    payload = emit_paper_predictions(
        alert_text="alert_name: boutique/startup/9",
        investigation_summary="",
        llm=llm,
    )

    assert payload is not None
    assert llm.invoked_with is not None
    user_content = llm.invoked_with["messages"][0]["content"]
    # The "no prior investigation" branch is what unblocks llm_alone mode.
    assert "No prior investigation evidence" in user_content


def _run_with_diagnosis(diagnosis: dict[str, Any]) -> Any:
    from tests.benchmarks._framework.adapters import RunResult

    return RunResult(
        case_id="c1",
        mode="opensre+llm",
        llm="gpt-4o",
        model_version="(test)",
        opensre_sha="(test)",
        started_at="2026-01-01T00:00:00+00:00",
        ended_at="2026-01-01T00:00:01+00:00",
        ok=True,
        error=None,
        final_diagnosis=diagnosis,
        evidence_entries=[],
        tokens_in=0,
        tokens_out=0,
        cost_usd=0.0,
        latency_ms=1000,
    )


def test_summarize_investigation_leads_with_conclusion_then_report() -> None:
    """Fix A: opensre's conclusion must come BEFORE the hedge-heavy report body
    so the predictor anchors on it. Component (when populated) leads, then the
    free-text root cause, then the supporting report."""
    from tests.benchmarks.cloudopsbench.adapter import _summarize_investigation

    run = _run_with_diagnosis(
        {
            "component": "app/redis-cart",
            "root_cause": "missing service account redis-service in boutique",
            "report": "## Findings\n• non-validated claims ...",
        }
    )
    summary = _summarize_investigation(run)
    assert "app/redis-cart" in summary
    assert "missing service account" in summary
    # Conclusion ordering: identified component / conclusion precede the report.
    assert summary.index("redis-cart") < summary.index("Findings")
    assert "Investigation conclusion" in summary


def test_summarize_investigation_handles_empty_component() -> None:
    """Component is empty in the current opensre output schema — the summary
    must still surface the free-text conclusion without crashing."""
    from tests.benchmarks.cloudopsbench.adapter import _summarize_investigation

    run = _run_with_diagnosis(
        {"component": "", "root_cause": "oom on cartservice", "report": "details"}
    )
    summary = _summarize_investigation(run)
    assert "oom on cartservice" in summary
    assert "Identified component" not in summary


def test_system_prompt_marks_investigation_summary_authoritative() -> None:
    """Fix A (2026-06-07): the 2026-06-06 run showed the predictor dropped the
    component opensre's report named from its top-3 on 15% of failures (3x the
    no-investigation arm). Root cause was the prompt telling the model rank-1 is
    'your strongest hypothesis given the evidence' — inviting it to re-diagnose
    and discard opensre's conclusion. The prompt must now mark a provided
    investigation summary as AUTHORITATIVE for rank-1."""
    from tests.benchmarks.cloudopsbench.predictor import _build_system_prompt

    prompt = _build_system_prompt()
    assert "AUTHORITATIVE" in prompt
    assert "re-diagnose" in prompt.lower() or "re-diagnose from the alert" in prompt
    # The alert-alone fallback (llm_alone control) must still be described so
    # that path is unchanged.
    assert "NO investigation summary" in prompt or "no investigation summary" in prompt


def test_user_prompt_with_summary_anchors_rank1_on_investigation() -> None:
    """The investigation-path user prompt must instruct the model to set rank-1
    to the investigation's identified component/root_cause, not re-derive it."""
    from tests.benchmarks.cloudopsbench.predictor import _build_user_prompt

    body = _build_user_prompt(
        "alert_name: trainticket/runtime/56",
        "ts-voucher-service Access denied for user 'ts'",
    )
    assert "INVESTIGATION SUMMARY" in body
    assert "rank 1" in body.lower()
    # Inputs are still carried through verbatim.
    assert "trainticket/runtime/56" in body
    assert "Access denied" in body


def test_user_prompt_includes_performance_localization_hint() -> None:
    from tests.benchmarks.cloudopsbench.predictor import _build_user_prompt

    body = _build_user_prompt(
        "alert_name: trainticket/performance/44",
        "cluster-wide CPU saturation under load",
        metric_alerts="Metric anomalies:\n  - ts-station-service: [LATENCY] +35691%",
        performance_localization_hint={
            "fault_object": "app/ts-station-service",
            "root_cause": "pod_network_delay",
            "rationale": "largest latency spike",
        },
    )
    assert "ALERT-DERIVED PERFORMANCE LOCALIZATION" in body
    assert "app/ts-station-service" in body
    assert "pod_network_delay" in body
    assert "35691" in body


def test_bridge_does_not_fire_on_realistic_investigation_text() -> None:
    """Pins the bridge's STRICT contract on text matching the kind of phrasing
    real investigations actually produce: pod-status terms ("ImagePullBackOff")
    + a known service name + a tag mention. The existing rule for
    ``incorrect_image_reference`` requires all of (``imagepullbackoff``,
    ``image pull``, ``incorrect image``) verbatim — the literal phrase
    ``incorrect image`` rarely appears in real LLM-generated investigation
    output, so the bridge stays silent on this realistic case and the
    predictor LLM still runs (no regression vs pre-bridge behavior).

    This documents WHY the bridge-before-predictor wiring evaluated on
    2026-06-07 was not shipped: offline replay against the 240 Fargate
    Fix-A cells showed the bridge fires on 3/120 cells per arm with zero
    net Δa1. See ``bench-results-openai/fix-a-loss-patterns.md``.
    """
    from tests.benchmarks.cloudopsbench.scoring import (
        infer_final_answer_from_opensre_text,
    )

    case_data = {
        "root_cause": "frontend deployment failing with ImagePullBackOff",
        "report": "image pull failed for frontend; tag v0.10.999 not found in registry",
        "final_state": {
            "root_cause": "frontend deployment failing with ImagePullBackOff",
            "report": "image pull failed for frontend; tag v0.10.999 not found in registry",
        },
    }
    # No "incorrect image" → strict AND-of-3-tokens rule misses → bridge silent.
    assert infer_final_answer_from_opensre_text(case_data) is None


def test_bridge_fires_when_all_required_tokens_present() -> None:
    """Pins the bridge's POSITIVE contract: when text DOES contain every token
    the rule requires (here ``imagepullbackoff`` + ``image pull`` + ``incorrect
    image``) AND a known service name, the bridge fires and emits the right
    rank-1 triple. Contrived input — the prior test documents that real LLM
    output rarely produces this exact phrase combination."""
    from tests.benchmarks.cloudopsbench.scoring import (
        infer_final_answer_from_opensre_text,
    )

    case_data = {
        "root_cause": "frontend ImagePullBackOff — incorrect image reference",
        "report": "image pull failed for frontend; incorrect image tag v0.10.999",
        "final_state": {
            "root_cause": "frontend ImagePullBackOff — incorrect image reference",
            "report": "image pull failed for frontend; incorrect image tag v0.10.999",
        },
    }
    payload = infer_final_answer_from_opensre_text(case_data)
    assert payload is not None
    rank1 = payload["top_3_predictions"][0]
    assert rank1["root_cause"] == "incorrect_image_reference"
    assert rank1["fault_object"] == "app/frontend"
    assert rank1["fault_taxonomy"] == "Startup_Fault"


def test_bridge_override_does_not_fire_without_known_service_name() -> None:
    """Confidence gate: the bridge requires BOTH a recognized root_cause keyword
    AND a known service/node/namespace name. Investigation text that only
    mentions a generic concept (no entity to localize) must return None so the
    predictor LLM still runs as fallback."""
    from tests.benchmarks.cloudopsbench.scoring import (
        infer_final_answer_from_opensre_text,
    )

    case_data = {
        "root_cause": "ImagePullBackOff observed in the cluster",
        "report": "Pods can't pull images",
        "final_state": {
            "root_cause": "ImagePullBackOff observed in the cluster",
            "report": "Pods can't pull images",
        },
    }
    payload = infer_final_answer_from_opensre_text(case_data)
    assert payload is None


def test_bridge_override_does_not_fire_on_empty_investigation() -> None:
    """The llm_alone control arm passes an empty investigation summary; the
    bridge must return None on empty input so llm_alone falls through to the
    predictor LLM as today — preserves the matched contrast."""
    from tests.benchmarks.cloudopsbench.scoring import (
        infer_final_answer_from_opensre_text,
    )

    payload = infer_final_answer_from_opensre_text(
        {"root_cause": "", "report": "", "final_state": {"root_cause": "", "report": ""}}
    )
    assert payload is None


def test_user_prompt_without_summary_is_alert_only_and_unchanged() -> None:
    """The llm_alone control path (empty summary) must NOT get the authoritative
    framing — it has no investigation to anchor on. Keeps the controls valid."""
    from tests.benchmarks.cloudopsbench.predictor import _build_user_prompt

    body = _build_user_prompt("alert_name: boutique/startup/9", "")
    assert "No prior investigation evidence" in body
    assert "AUTHORITATIVE" not in body


def test_emit_paper_predictions_returns_none_when_llm_raises() -> None:
    """Predictor is best-effort: LLM failure must NOT break scoring."""
    llm = _FakeLLM("", raise_on_invoke=True)

    payload = emit_paper_predictions(
        alert_text="alert_name: anything",
        investigation_summary="anything",
        llm=llm,
    )

    assert payload is None


def test_emit_paper_predictions_returns_none_when_response_unparseable() -> None:
    llm = _FakeLLM("the model rambled and never produced JSON")

    payload = emit_paper_predictions(
        alert_text="alert_name: anything",
        investigation_summary="anything",
        llm=llm,
    )

    assert payload is None


# --------------------------------------------------------------------------- #
# Taxonomy derivation — LLM's fault_taxonomy is overridden with the           #
# deterministic mapping from scoring._taxonomy_for_root_cause.                #
# --------------------------------------------------------------------------- #


def test_parse_predictions_overrides_wrong_llm_taxonomy_with_derived() -> None:
    """Real failure mode observed in the June-3 OpenAI bench run:
    `trainticket/runtime/34` — gpt-5 correctly identified
    ``mysql_invalid_credentials`` but labelled it ``Startup_Fault``
    (because the failure surfaces during startup). The paper-derived
    taxonomy is ``Runtime_Fault``. Without this override, the case
    scored a1=0 despite a substantively correct diagnosis."""
    text = (
        '{"top_3_predictions": ['
        '{"rank": 1, "fault_taxonomy": "Startup_Fault",'
        ' "fault_object": "app/ts-auth-service",'
        ' "root_cause": "mysql_invalid_credentials"}'
        "]}"
    )
    parsed = _parse_predictions(text)
    assert parsed is not None
    pred = parsed["top_3_predictions"][0]
    # LLM said Startup_Fault, but mysql_invalid_credentials → Runtime_Fault.
    assert pred["fault_taxonomy"] == "Runtime_Fault"
    # The other fields must be preserved verbatim.
    assert pred["fault_object"] == "app/ts-auth-service"
    assert pred["root_cause"] == "mysql_invalid_credentials"


def test_parse_predictions_overrides_wrong_taxonomy_for_missing_secret_binding() -> None:
    """`trainticket/startup/14` close-miss in the June-3 run. Tests the
    scoring mapping fix (previously Runtime_Fault → now Startup_Fault per
    dataset ground truth)."""
    text = (
        '{"top_3_predictions": ['
        '{"rank": 1, "fault_taxonomy": "Admission_Fault",'
        ' "fault_object": "app/ts-security-service",'
        ' "root_cause": "missing_secret_binding"}'
        "]}"
    )
    parsed = _parse_predictions(text)
    assert parsed is not None
    assert parsed["top_3_predictions"][0]["fault_taxonomy"] == "Startup_Fault"


def test_parse_predictions_overrides_wrong_taxonomy_for_sidecar_port_conflict() -> None:
    """`trainticket/runtime/90` close-miss in the June-3 run."""
    text = (
        '{"top_3_predictions": ['
        '{"rank": 1, "fault_taxonomy": "Service_Routing_Fault",'
        ' "fault_object": "app/ts-route-service",'
        ' "root_cause": "service_sidecar_port_conflict"}'
        "]}"
    )
    parsed = _parse_predictions(text)
    assert parsed is not None
    assert parsed["top_3_predictions"][0]["fault_taxonomy"] == "Runtime_Fault"


def test_parse_predictions_keeps_taxonomy_when_llm_already_correct() -> None:
    """When the LLM happens to pick the right taxonomy, the derivation
    produces the same value — no behavior change."""
    text = (
        '{"top_3_predictions": ['
        '{"rank": 1, "fault_taxonomy": "Runtime_Fault",'
        ' "fault_object": "app/frontend",'
        ' "root_cause": "oom_killed"}'
        "]}"
    )
    parsed = _parse_predictions(text)
    assert parsed is not None
    assert parsed["top_3_predictions"][0]["fault_taxonomy"] == "Runtime_Fault"


def test_parse_predictions_fills_taxonomy_when_llm_omits_it() -> None:
    """If the LLM omits fault_taxonomy entirely, the derived value is
    still emitted — the prediction stays valid for scoring."""
    text = (
        '{"top_3_predictions": ['
        '{"rank": 1, "fault_object": "app/frontend",'
        ' "root_cause": "oom_killed"}'
        "]}"
    )
    parsed = _parse_predictions(text)
    assert parsed is not None
    assert parsed["top_3_predictions"][0]["fault_taxonomy"] == "Runtime_Fault"


def test_parse_predictions_derives_default_taxonomy_for_unknown_root_cause() -> None:
    """An LLM-emitted root_cause outside the enum falls back to the
    mapping's default (``Performance_Fault``). Validates graceful degradation
    rather than crashing."""
    text = (
        '{"top_3_predictions": ['
        '{"rank": 1, "fault_taxonomy": "Runtime_Fault",'
        ' "fault_object": "app/frontend",'
        ' "root_cause": "some_unknown_root_cause_outside_the_enum"}'
        "]}"
    )
    parsed = _parse_predictions(text)
    assert parsed is not None
    assert parsed["top_3_predictions"][0]["fault_taxonomy"] == "Performance_Fault"


# --------------------------------------------------------------------------- #
# Rate-limit retry behavior — predictor-specific glue (the recognizer +       #
# retry helper itself are tested in tests/utils/test_llm_retry.py).           #
# --------------------------------------------------------------------------- #


class _FlakyLLM:
    """Raises rate-limit on the first N calls, then returns canned content."""

    def __init__(self, content: str, fail_first_n: int) -> None:
        self._content = content
        self._remaining_failures = fail_first_n
        self.call_count = 0

    def invoke(
        self,
        _messages: list[dict[str, Any]],
        system: str | None = None,  # noqa: ARG002 — interface contract
    ) -> _FakeLLMResponse:
        self.call_count += 1
        if self._remaining_failures > 0:
            self._remaining_failures -= 1
            raise RuntimeError(
                "OpenAI rate limit exceeded: Error code: 429 - tokens per min (TPM): "
                "Limit 30000, Used 29248. Please try again in 94ms."
            )
        return _FakeLLMResponse(content=self._content)


def test_emit_paper_predictions_recovers_after_transient_rate_limit(monkeypatch) -> None:
    """Two transient 429s, then success — payload must come back populated."""
    # Don't actually sleep during the test.
    import services.llm_retry as llm_retry

    monkeypatch.setattr(llm_retry.time, "sleep", lambda _s: None)

    llm = _FlakyLLM(
        content=(
            '{"top_3_predictions": [{"rank": 1, "fault_taxonomy": "Runtime_Fault",'
            ' "fault_object": "app/frontend", "root_cause": "oom_killed"}]}'
        ),
        fail_first_n=2,
    )

    payload = emit_paper_predictions(
        alert_text="alert_name: anything",
        investigation_summary="anything",
        llm=llm,
    )

    assert payload is not None
    assert payload["top_3_predictions"][0]["root_cause"] == "oom_killed"
    # 2 failures + 1 success = 3 calls.
    assert llm.call_count == 3


def test_emit_paper_predictions_gives_up_after_max_rate_limit_retries(monkeypatch) -> None:
    """If every retry hits the rate limit, return None gracefully (no crash)."""
    import services.llm_retry as llm_retry

    monkeypatch.setattr(llm_retry.time, "sleep", lambda _s: None)

    llm = _FlakyLLM(content="unused", fail_first_n=99)

    payload = emit_paper_predictions(
        alert_text="alert_name: anything",
        investigation_summary="anything",
        llm=llm,
    )

    assert payload is None
    # Should have attempted exactly DEFAULT_MAX_ATTEMPTS times.
    assert llm.call_count == llm_retry.DEFAULT_MAX_ATTEMPTS


def test_emit_paper_predictions_does_not_retry_non_rate_limit_errors(monkeypatch) -> None:
    """A 400 / schema error should fail fast — no point retrying a deterministic bug."""
    import services.llm_retry as llm_retry

    monkeypatch.setattr(llm_retry.time, "sleep", lambda _s: None)

    class _BrokenLLM:
        def __init__(self) -> None:
            self.call_count = 0

        def invoke(
            self,
            _messages: list[dict[str, Any]],
            system: str | None = None,  # noqa: ARG002 — interface contract
        ) -> Any:
            self.call_count += 1
            raise RuntimeError("Anthropic request rejected (HTTP 400): invalid schema")

    llm = _BrokenLLM()

    payload = emit_paper_predictions(
        alert_text="alert_name: anything",
        investigation_summary="anything",
        llm=llm,
    )

    assert payload is None
    # No retries on deterministic failures.
    assert llm.call_count == 1
