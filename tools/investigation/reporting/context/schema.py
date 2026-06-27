"""Typed context consumed by publish-findings formatters."""

from __future__ import annotations

from typing import Any

from typing_extensions import TypedDict


class ReportContext(TypedDict, total=False):
    """Data extracted from state for report formatting.

    Contains all information needed to generate the final RCA report,
    including pipeline metadata, root cause analysis results, validated claims,
    infrastructure assets, and evidence references.
    """

    # Core RCA results
    pipeline_name: str
    alert_name: str | None
    root_cause: str
    validated_claims: list[dict]
    non_validated_claims: list[dict]
    remediation_steps: list[str]
    correlation: dict[str, Any]

    # S3 verification
    s3_marker_exists: bool

    # CloudWatch metadata
    cloudwatch_log_group: str | None
    cloudwatch_log_stream: str | None
    cloudwatch_logs_url: str | None
    cloudwatch_region: str | None
    alert_id: str | None
    evidence_catalog: dict
    investigation_duration_seconds: int | None

    # Raw data for deeper inspection
    evidence: dict
    raw_alert: dict

    # Tool call history for investigation transparency
    executed_hypotheses: list[dict]

    # Integration endpoints (for building deep links)
    grafana_endpoint: str | None
    datadog_site: str | None

    # Concrete source provenance, keyed by source name (grafana, eks, github, ...)
    source_provenance: dict[str, dict[str, str]]

    # Alert severity (e.g. critical, high) for channel-specific formatting (Telegram, etc.)
    severity: str | None

    kube_pod_name: str | None
    kube_container_name: str | None
    kube_namespace: str | None

    # Multiple failed pods (for cluster-scale failures)
    kube_failed_pods: list[dict]
