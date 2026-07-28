"""Behavioral test for intake/node.py after routing through llm_resolution (T-5, #4439).

Constructs a fake structured-output LLM client (no real network calls, no
get_llm()) and drives extract_alert() end to end to confirm it still returns
the expected state-update dict shape now that the LLM comes from
default_reasoning_llm_factory() instead of a direct get_llm() call.
"""

from __future__ import annotations

from typing import Any, cast
from unittest.mock import patch

from core.domain.alerts.extraction import AlertDetails
from core.state import InvestigationState
from tools.investigation.stages.intake.node import extract_alert


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


def test_extract_alert_uses_injected_llm_and_returns_state_updates() -> None:
    fake_details = AlertDetails(is_noise=False, alert_name="Pipeline Error", severity="critical")
    state = cast(InvestigationState, {"raw_alert": {"alert_id": "a1", "text": "boom"}})

    with patch(
        "tools.investigation.stages.intake.node.default_reasoning_llm_factory",
        return_value=_FakeStructuredOutputLLM(fake_details),
    ):
        result = extract_alert(state)

    assert isinstance(result, dict)
    assert result["is_noise"] is False
    assert result["alert_name"] == "Pipeline Error"
    assert result["severity"] == "critical"


def test_extract_alert_noise_classification_short_circuits() -> None:
    fake_details = AlertDetails(is_noise=True, alert_name="chat", severity="info")
    state = cast(InvestigationState, {"raw_alert": {"alert_id": "a2", "text": "thanks!"}})

    with patch(
        "tools.investigation.stages.intake.node.default_reasoning_llm_factory",
        return_value=_FakeStructuredOutputLLM(fake_details),
    ):
        result = extract_alert(state)

    assert result == {"is_noise": True}
