"""Tests for sample-alert templates and their human-readable summary."""

from __future__ import annotations

import pytest

from app.cli.investigation.alert_templates import (
    build_alert_template,
    summarize_alert_template,
)


def test_summarize_generic_template_includes_core_fields() -> None:
    pairs = summarize_alert_template(build_alert_template("generic"))
    summary = dict(pairs)

    assert summary["Alert"] == "High error rate in payments ETL"
    assert summary["Pipeline"] == "payments_etl"
    assert summary["Severity"] == "critical"
    assert summary["Source"] == "generic"
    assert "database connection errors" in summary["Summary"]
    # Labels stay in a stable, predictable order for rendering.
    assert [label for label, _ in pairs] == [
        "Alert",
        "Pipeline",
        "Severity",
        "Source",
        "Summary",
    ]


@pytest.mark.parametrize(
    "template_name",
    ["generic", "datadog", "grafana", "honeycomb", "coralogix", "splunk"],
)
def test_summarize_every_template_yields_named_alert(template_name: str) -> None:
    pairs = summarize_alert_template(build_alert_template(template_name))
    summary = dict(pairs)

    # Every template should at least identify the alert, a subject, and severity.
    assert summary.get("Alert")
    assert summary.get("Pipeline")
    assert summary.get("Severity") == "critical"
    # Only non-empty fields are emitted.
    assert all(value for _, value in pairs)


def test_summarize_falls_back_to_title_and_annotation_summary() -> None:
    pairs = summarize_alert_template(
        {
            "title": "[FIRING] checkout latency",
            "service_name": "checkout-api",
            "commonAnnotations": {"summary": "spans timing out"},
        }
    )
    summary = dict(pairs)

    assert summary["Alert"] == "[FIRING] checkout latency"
    assert summary["Pipeline"] == "checkout-api"
    assert summary["Summary"] == "spans timing out"
    assert "Severity" not in summary


def test_summarize_empty_payload_returns_no_pairs() -> None:
    assert summarize_alert_template({}) == []
