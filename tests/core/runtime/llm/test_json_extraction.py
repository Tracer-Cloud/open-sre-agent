"""JSON recovery from model text, and what happens when the output budget runs out.

The production failure these pin: a diagnose-stage completion hit its
``max_tokens`` ceiling, so the JSON payload stopped mid-value. The previous
regex cascade could not parse an unbalanced payload at all, the stage reported
"Structured diagnosis parse failed", and a whole investigation was discarded.
"""

from __future__ import annotations

import logging

import pytest
from pydantic import BaseModel

from core.llm.shared.json_extraction import extract_json_payload
from core.llm.shared.structured_output import StructuredOutputClient
from core.llm.types import LLMResponse

# Wrapped payloads: prose, fences, and prose *after* the fence. A greedy
# ``\{.*\}`` over-captures the trailing ``{key}``; offset-based decoding cannot.
_WRAPPED_CASES = [
    ('```json\n{"a": 1}\n```', {"a": 1}),
    ('Here is the JSON:\n\n```json\n{"location": "node.py"}\n```', {"location": "node.py"}),
    (
        'Sure!\n\n```json\n{"key": "value"}\n```\n\nThe {key} field is the identifier.',
        {"key": "value"},
    ),
    ('{"a": 1}', {"a": 1}),
]

# A diagnose payload cut off mid-string, exactly as a max_tokens stop leaves it:
# no closing quote, no closing bracket, no closing brace, no closing fence.
_TRUNCATED_DIAGNOSIS = """{
  "root_cause": "The example-api deployment caps memory at 256Mi.",
  "root_cause_category": "pod_oomkilled",
  "validated_claims": [
    "the last termination reports exit code 137",
    "the deployment spec caps memory at 256Mi"
  ],
  "remediation_steps": [
    "raise the memory limit to 512Mi",
    "re-run the load profile to conf"""


@pytest.mark.parametrize(("text", "expected"), _WRAPPED_CASES)
def test_wrapped_payload_is_decoded_at_its_own_boundary(text: str, expected: dict) -> None:
    assert extract_json_payload(text) == expected


def test_truncated_payload_keeps_every_completed_field() -> None:
    """The live bug: a payload cut at max_tokens must not be a total loss.

    The outer object is what the caller validates, so recovery has to return
    that — not the first nested array that happens to be complete.
    """
    payload = extract_json_payload(_TRUNCATED_DIAGNOSIS)

    assert payload["root_cause_category"] == "pod_oomkilled"
    assert payload["validated_claims"] == [
        "the last termination reports exit code 137",
        "the deployment spec caps memory at 256Mi",
    ]
    # The half-written element is dropped; the finished one survives.
    assert payload["remediation_steps"] == ["raise the memory limit to 512Mi"]


def test_truncation_inside_an_unterminated_fence_is_still_recovered() -> None:
    """A completion that dies mid-payload never emits its closing ``` fence."""
    text = (
        "Here is the diagnosis:\n\n```json\n{\n"
        '  "root_cause": "disk filled on node-1",\n'
        '  "validated_claims": ["df reported 100% on /var'
    )

    assert extract_json_payload(text) == {"root_cause": "disk filled on node-1"}


def test_malformed_payload_is_not_repaired_into_a_guess() -> None:
    """A value that closes on its own is broken, not truncated — do not invent one."""
    with pytest.raises(ValueError, match="did not return valid JSON payload"):
        extract_json_payload('{"root_cause": }')


def test_failure_message_reports_how_much_text_came_back() -> None:
    """Length separates "the model said nothing" from "the model wrote prose"."""
    with pytest.raises(ValueError, match=r"valid JSON payload \(32 chars returned\)"):
        extract_json_payload("This is plain text with no JSON.")


class _Payload(BaseModel):
    root_cause: str


class _TruncatingLLM:
    """Returns a payload cut off at the output ceiling, the way the provider does."""

    def __init__(self, content: str, finish_reason: str | None) -> None:
        self._content = content
        self._finish_reason = finish_reason

    def invoke(self, _prompt: str) -> LLMResponse:
        return LLMResponse(content=self._content, finish_reason=self._finish_reason)


def test_output_limit_is_named_in_the_log_when_a_payload_is_truncated(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Without this line the operator sees a parse error and tunes the prompt.

    The actionable fix is raising ``max_tokens`` for the role, which is only
    discoverable from ``finish_reason``.
    """
    client = StructuredOutputClient(
        _TruncatingLLM('{"root_cause": "disk filled on node-1", "next', "length"), _Payload
    )

    with caplog.at_level(logging.WARNING, logger="core.llm.shared.structured_output"):
        result = client.invoke("diagnose")

    assert result.root_cause == "disk filled on node-1"
    assert "stopped at the model's output limit" in caplog.text
    assert "finish_reason=length" in caplog.text


def test_unrecoverable_payload_reports_the_stop_reason() -> None:
    """The caller logs this string; without the stop reason it cannot be triaged."""
    client = StructuredOutputClient(
        _TruncatingLLM("I could not complete that.", "length"), _Payload
    )

    with pytest.raises(ValueError, match=r"finish_reason='length'"):
        client.invoke("diagnose")
