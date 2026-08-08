from __future__ import annotations

from unittest.mock import patch

from tools.investigation.stages.intake.node import _build_alert_updates


def test_intake_preserves_caller_resolved_incident_window() -> None:
    window = {
        "_schema_version": 1,
        "since": "2026-04-21T11:00:00Z",
        "until": "2026-04-21T13:00:00Z",
        "source": "caller_override",
        "confidence": 1.0,
    }
    details = type(
        "Details",
        (),
        {
            "is_noise": False,
            "alert_name": "Incident",
            "severity": "warning",
            "alert_source": "grafana",
            "model_dump": lambda self: {},
        },
    )()

    with (
        patch("tools.investigation.stages.intake.node.enrich_raw_alert", return_value={}),
        patch("tools.investigation.stages.intake.node.make_problem_md", return_value="problem"),
    ):
        updates = _build_alert_updates(
            {"incident_window": window, "investigation_started_at": 1.0},
            "raw alert",
            details,
        )

    assert updates["incident_window"] == window
