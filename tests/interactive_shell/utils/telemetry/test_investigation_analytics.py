"""Tests for the investigation outcome analytics bridge."""

from __future__ import annotations

import pytest

from surfaces.interactive_shell.ui.investigation_outcome import InvestigationOutcome
from surfaces.interactive_shell.utils.telemetry.investigation_analytics import (
    publish_investigation_outcome_analytics,
)


def _capture_outcome_calls(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, object]]:
    calls: list[dict[str, object]] = []
    monkeypatch.setattr(
        "surfaces.interactive_shell.utils.telemetry.investigation_analytics."
        "capture_investigation_outcome",
        lambda **kwargs: calls.append(kwargs),
    )
    monkeypatch.setattr(
        "surfaces.interactive_shell.utils.telemetry.investigation_analytics."
        "capture_investigation_cancelled",
        lambda **_kwargs: None,
    )
    return calls


def test_completed_outcome_omits_placeholder_failure_properties(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = _capture_outcome_calls(monkeypatch)
    publish_investigation_outcome_analytics(
        InvestigationOutcome(
            status="completed",
            target="generic",
            investigation_id="inv-1",
            final_state={"root_cause": "disk full"},
        )
    )
    assert len(calls) == 1
    call = calls[0]
    assert call["status"] == "completed"
    assert call["root_cause_excerpt"] == "disk full"
    assert call["error_excerpt"] == ""
    assert call["failure_category"] is None
    assert call["integration_involved"] is None
    assert call["integration_failure_message"] is None
    assert call["failure_detail"] is None


def test_failed_outcome_keeps_failure_properties(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = _capture_outcome_calls(monkeypatch)
    publish_investigation_outcome_analytics(
        InvestigationOutcome(
            status="failed",
            target="generic",
            investigation_id="inv-2",
            error_message="grafana query failed: 401",
            error_detail="RuntimeError: grafana query failed: 401",
            failure_category="integration",
            integration_involved="grafana",
            integration_failure_message="grafana query failed: 401",
        )
    )
    assert len(calls) == 1
    call = calls[0]
    assert call["status"] == "failed"
    assert call["error_excerpt"] == "grafana query failed: 401"
    assert call["failure_category"] == "integration"
    assert call["integration_involved"] == "grafana"
    assert call["failure_detail"] == "RuntimeError: grafana query failed: 401"
