from __future__ import annotations

import pytest

from core.domain.diagnosis import (
    InvestigationResult,
    synthesize_diagnosis_text_from_evidence,
)
from tools.investigation.stages.diagnose import node


def test_synthesize_diagnosis_text_from_evidence_includes_tool_summaries() -> None:
    text = synthesize_diagnosis_text_from_evidence(
        {"alertmanager_alerts": [{"status": "firing"}]},
        [{"tool": "alertmanager", "summary": "2 firing alerts on checkout"}],
        alert_name="CheckoutErrors",
    )
    assert "CheckoutErrors" in text
    assert "alertmanager" in text
    assert "2 firing alerts" in text
    assert "firing" in text


def test_parse_diagnosis_falls_back_to_evidence_when_assistant_text_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: list[str] = []

    def _fake_structured(
        last_text: str,
        evidence: dict,
        *,
        alert_source: str = "",
    ) -> InvestigationResult:
        captured.append(last_text)
        return InvestigationResult(
            root_cause="Alertmanager shows checkout failures",
            root_cause_category="application_tier_load_spike",
            validity_score=0.6,
        )

    monkeypatch.setattr(node, "_parse_via_structured_output", _fake_structured)

    result = node.parse_diagnosis(
        [{"role": "assistant", "content": ""}],
        {"alertmanager_alerts": [{"status": "firing"}]},
        alert_name="CheckoutErrors",
        alert_source="alertmanager",
        evidence_entries=[{"tool": "alertmanager", "summary": "2 firing alerts"}],
    )

    assert captured
    assert "alertmanager" in captured[0]
    assert result.root_cause == "Alertmanager shows checkout failures"


def test_parse_diagnosis_returns_unknown_without_text_or_evidence() -> None:
    result = node.parse_diagnosis([], {}, alert_name="SilentAlert")
    assert "insufficient evidence" in result.root_cause.lower()


def test_parse_diagnosis_logs_when_evidence_synthesis_is_used(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    monkeypatch.setattr(
        node,
        "_parse_via_structured_output",
        lambda *_args, **_kwargs: InvestigationResult(
            root_cause="ok",
            root_cause_category="unknown",
            validity_score=0.5,
        ),
    )

    with caplog.at_level("INFO", logger=node.logger.name):
        node.parse_diagnosis(
            [{"role": "assistant", "content": ""}],
            {"alertmanager_alerts": [{"status": "firing"}]},
            alert_name="CheckoutErrors",
            evidence_entries=[{"tool": "alertmanager", "summary": "2 firing alerts"}],
        )

    assert any("synthesizing diagnosis prompt" in record.message for record in caplog.records)
