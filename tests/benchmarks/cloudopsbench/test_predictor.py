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
