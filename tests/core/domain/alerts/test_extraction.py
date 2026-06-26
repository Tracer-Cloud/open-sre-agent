from __future__ import annotations

from core.domain.alerts.extraction import (
    fallback_details,
    needs_full_json_prompt,
)


def test_needs_full_json_prompt_for_alertmanager_shape() -> None:
    payload = {
        "commonLabels": {"alertname": "HighCPU"},
        "alerts": [{"startsAt": "2026-01-01T00:00:00Z"}],
    }
    assert needs_full_json_prompt(payload) is True


def test_fallback_details_reads_alertmanager_labels() -> None:
    raw_alert = {
        "commonLabels": {"alertname": "DiskFull", "severity": "critical", "service": "api"},
        "commonAnnotations": {"pipeline_name": "billing"},
    }
    details = fallback_details({}, raw_alert)
    assert details.alert_name == "DiskFull"
    assert details.pipeline_name == "api"
    assert details.severity == "critical"
    assert details.is_noise is False
