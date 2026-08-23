from __future__ import annotations

from typing import Any

import pytest

from core.domain.diagnosis import InvestigationResult
from tools.investigation.stages.diagnose import node


class _StructuredDiagnosisCall:
    def __init__(self, schema: Any) -> None:
        self._schema = schema
        self.prompt: str = ""

    def with_config(self, **_kwargs: Any) -> _StructuredDiagnosisCall:
        return self

    def invoke(self, prompt: str) -> Any:
        self.prompt = prompt
        return self._schema.model_validate(
            {
                "root_cause": "The checkout deployment exhausted its connection pool.",
                "root_cause_category": "unknown",
                "validity_score": 0.8,
            }
        )


class _ReasoningLLM:
    last_call: _StructuredDiagnosisCall | None = None

    @staticmethod
    def with_structured_output(schema: Any) -> _StructuredDiagnosisCall:
        _ReasoningLLM.last_call = _StructuredDiagnosisCall(schema)
        return _ReasoningLLM.last_call


def test_diagnosis_parsing_uses_injected_harness_reasoning_client(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(node, "default_reasoning_llm_factory", _ReasoningLLM)

    result = node._parse_via_structured_output(  # noqa: SLF001 - stage seam under test
        "Root cause: connection pool exhausted.",
        {"query_logs": {"errors": 12}},
    )

    assert isinstance(result, InvestigationResult)
    assert "connection pool" in result.root_cause


def test_structured_output_prompt_instructs_exact_evidence_tags(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(node, "default_reasoning_llm_factory", _ReasoningLLM)

    node._parse_via_structured_output(  # noqa: SLF001 - stage seam under test
        "Root cause: connection pool exhausted.",
        {"victoria_logs_query#1": {"rows": []}, "victoria_logs_query#2": {"rows": []}},
    )

    assert _ReasoningLLM.last_call is not None
    assert "[evidence: " in _ReasoningLLM.last_call.prompt
    assert "exact numbered key" in _ReasoningLLM.last_call.prompt
