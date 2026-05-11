"""Shared constants for root cause diagnosis prompt construction."""

from __future__ import annotations

# Allowed evidence sources the model can reference (keeps grounding consistent)
ALLOWED_EVIDENCE_SOURCES = [
    "aws_batch_jobs",
    "tracer_tools",
    "logs",
    "cloudwatch_logs",
    "host_metrics",
    "aws_cloudwatch_metrics",
    "aws_rds_events",
    "aws_performance_insights",
    "lambda_logs",
    "lambda_code",
    "lambda_config",
    "s3_metadata",
    "s3_audit",
    "vendor_audit",
    "grafana_logs",
    "grafana_traces",
    "grafana_metrics",
    "grafana_alert_rules",
    "datadog_logs",
    "datadog_monitors",
    "datadog_events",
    "betterstack_logs",
    "vercel",
    "github",
    "cloudopsbench_evidence",
]

GRAFANA_SOURCE_TYPE_LABELS: dict[str, str] = {
    "aws_performance_insights": "Performance Insights",
    "aws_rds_events": "RDS Events",
    "cloudwatch_logs": "CloudWatch Logs",
    "datadog_logs": "Datadog Logs",
    "db-instance": "RDS Event",
    "grafana_loki": "Grafana Logs",
    "opensre_log": "OpenSRE Log",
    "rds_enhanced_monitoring": "RDS Enhanced Monitoring",
}
