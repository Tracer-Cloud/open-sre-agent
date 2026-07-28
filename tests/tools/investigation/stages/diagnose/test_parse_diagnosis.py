"""Behavioral test for diagnose/node.py after routing through llm_resolution (T-5, #4439).

Constructs a fake structured-output LLM client (no real network calls, no
get_llm()) and drives parse_diagnosis() end to end to confirm it still
returns an InvestigationResult now that the LLM comes from
default_reasoning_llm_factory() instead of a direct get_llm() call.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import patch

from core.domain.diagnosis import InvestigationResult
from tools.investigation.stages.diagnose.node import parse_diagnosis


class _FakeStructuredOutputLLM:
    """Stands in for the reasoning LLM's with_structured_output(...).with_config(...).invoke(...) chain."""

    def __init__(self, result: Any) -> None:
        self._result = result

    def with_structured_output(self, _schema: Any) -> _FakeStructuredOutputLLM:
        return self

    def with_config(self, **_kwargs: Any) -> _FakeStructuredOutputLLM:
        return self

    def invoke(self, _prompt: str) -> Any:
        return self._result


def test_parse_diagnosis_uses_injected_llm_and_returns_investigation_result() -> None:
    messages = [{"role": "assistant", "content": "Root cause: the pipeline timed out."}]
    fake_payload = {
        "root_cause": "The pipeline timed out waiting on a downstream dependency.",
        "root_cause_category": "Infrastructure",
    }

    with patch(
        "tools.investigation.stages.diagnose.node.default_reasoning_llm_factory",
        return_value=_FakeStructuredOutputLLM(fake_payload),
    ):
        result = parse_diagnosis(messages, evidence={}, alert_name="Pipeline Error")

    assert isinstance(result, InvestigationResult)
    assert result.root_cause == fake_payload["root_cause"]
    assert result.root_cause_category.lower() == "infrastructure"


def test_parse_diagnosis_falls_back_when_llm_raises() -> None:
    messages = [{"role": "assistant", "content": "Root cause: unclear, see logs."}]

    class _RaisingLLM(_FakeStructuredOutputLLM):
        def invoke(self, _prompt: str) -> Any:
            raise RuntimeError("simulated structured-output failure")

    with patch(
        "tools.investigation.stages.diagnose.node.default_reasoning_llm_factory",
        return_value=_RaisingLLM(None),
    ):
        result = parse_diagnosis(messages, evidence={}, alert_name="Pipeline Error")

    assert isinstance(result, InvestigationResult)
