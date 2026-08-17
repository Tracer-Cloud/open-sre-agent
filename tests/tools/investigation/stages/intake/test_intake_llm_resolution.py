from __future__ import annotations

from typing import Any

import pytest

from core.domain.alerts.extraction import AlertDetails
from tools.investigation.stages.intake import node


class _StructuredAlertCall:
    def with_config(self, **_kwargs: Any) -> _StructuredAlertCall:
        return self

    @staticmethod
    def invoke(_prompt: str) -> AlertDetails:
        return AlertDetails(
            alert_name="Checkout errors",
            severity="critical",
            is_noise=False,
            alert_source="grafana",
        )


class _ReasoningLLM:
    @staticmethod
    def with_structured_output(_schema: Any) -> _StructuredAlertCall:
        return _StructuredAlertCall()


def test_alert_extraction_uses_injected_harness_reasoning_client(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(node, "default_reasoning_llm_factory", _ReasoningLLM)

    details = node._extract_alert_details(  # noqa: SLF001 - stage seam under test
        {"raw_alert": {"message": "checkout is returning 500s"}}  # type: ignore[arg-type]
    )

    assert isinstance(details, AlertDetails)
    assert details.alert_name == "Checkout errors"
