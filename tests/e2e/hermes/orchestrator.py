"""Shared orchestration helpers for Hermes e2e investigation scenarios."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from app.pipeline.runners import run_investigation
from app.utils.tracing import traceable
from tests.synthetic.hermes_rca.scenario_loader import SUITE_DIR, load_scenario
from tests.synthetic.mock_hermes_backend.backend import FixtureHermesBackend
from tests.utils.alert_factory.factory import create_alert


def _build_annotations(scenario_id: str, scenario_alert: dict[str, Any]) -> dict[str, Any]:
    labels = scenario_alert.get("commonLabels") or {}
    annotations = scenario_alert.get("commonAnnotations") or {}
    return {
        "context_sources": "hermes",
        "scenario_id": scenario_id,
        "failure_mode": annotations.get("failure_mode", "unknown"),
        "hermes_session_id": annotations.get("hermes_session_id", ""),
        "hermes_provider": annotations.get("hermes_provider", ""),
        "hermes_model": annotations.get("hermes_model", ""),
        "severity": labels.get("severity", "critical"),
    }


def run_hermes_scenario(scenario_id: str) -> dict[str, Any]:
    fixture = load_scenario(SUITE_DIR / scenario_id)

    session_id = fixture.session_id()

    raw_alert = create_alert(
        pipeline_name="hermes-long-running-e2e",
        run_name=f"{scenario_id}-{uuid.uuid4().hex[:8]}",
        status="failed",
        timestamp=datetime.now(UTC).isoformat(),
        severity=str((fixture.alert.get("commonLabels") or {}).get("severity", "critical")),
        alert_name=str(fixture.alert.get("title") or scenario_id),
        annotations=_build_annotations(scenario_id, fixture.alert),
    )

    resolved_integrations = {
        "hermes": {
            "connection_verified": True,
            "session_id": session_id,
            "_backend": FixtureHermesBackend(fixture),
        }
    }

    @traceable(
        run_type="chain",
        name=f"hermes_e2e_{scenario_id}",
        metadata={
            "scenario_id": scenario_id,
            "context_sources": "hermes",
            "session_id": session_id,
        },
    )
    def _invoke() -> dict[str, Any]:
        return dict(run_investigation(raw_alert, resolved_integrations=resolved_integrations))

    return _invoke()
