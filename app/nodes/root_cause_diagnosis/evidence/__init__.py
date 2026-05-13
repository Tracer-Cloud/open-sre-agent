"""Evidence section assembly for root cause diagnosis prompts."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from app.state import InvestigationState

from .aws import (
    build_batch_section,
    build_cloudwatch_section,
    build_error_logs_section,
    build_failed_tools_section,
    build_host_metrics_section,
    build_lambda_config_section,
    build_lambda_function_section,
    build_lambda_logs_section,
    build_performance_insights_section,
    build_rds_events_section,
    build_rds_metrics_section,
    build_s3_audit_section,
    build_s3_object_section,
    build_vendor_audit_section,
)
from .datadog import (
    build_datadog_events_section,
    build_datadog_logs_section,
    build_datadog_monitors_section,
    build_datadog_pods_section,
)
from .github import build_github_section
from .grafana import (
    build_grafana_alert_rules_section,
    build_grafana_logs_section,
    build_grafana_metrics_section,
    build_grafana_spans_section,
)
from .misc import (
    build_alert_annotations_section,
    build_betterstack_section,
    build_cloudopsbench_section,
    build_openclaw_section,
)
from .vercel import build_vercel_section

if TYPE_CHECKING:
    from app.masking import MaskingContext


def build_evidence_sections(
    state: InvestigationState,
    evidence: dict[str, Any],
    masking_ctx: MaskingContext | None = None,
) -> str:
    """Build all evidence sections for the prompt, in canonical order."""
    # Parse alert context from raw_alert
    raw_alert = state.get("raw_alert", {})
    cloudwatch_url: str | None = None
    vercel_url: str | None = None
    alert_annotations: dict[str, Any] = {}
    raw_alert_text: str = ""

    if isinstance(raw_alert, str):
        raw_alert_text = masking_ctx.mask(raw_alert) if masking_ctx else raw_alert
    elif isinstance(raw_alert, dict):
        cloudwatch_url = raw_alert.get("cloudwatch_logs_url") or raw_alert.get("cloudwatch_url")
        vercel_url = raw_alert.get("vercel_log_url") or raw_alert.get("vercel_url")
        alert_annotations = (
            raw_alert.get("annotations", {}) or raw_alert.get("commonAnnotations", {}) or {}
        )

    sections: list[str] = []

    # AWS evidence
    sections.append(build_cloudwatch_section(evidence, cloudwatch_url=cloudwatch_url))
    sections.append(build_batch_section(evidence))
    sections.append(build_failed_tools_section(evidence))
    sections.append(build_error_logs_section(evidence))
    sections.append(build_host_metrics_section(evidence))
    sections.append(build_rds_metrics_section(evidence))
    sections.append(build_rds_events_section(evidence))
    sections.append(build_performance_insights_section(evidence))
    sections.append(build_lambda_logs_section(evidence))
    sections.append(build_lambda_function_section(evidence))
    sections.append(build_lambda_config_section(evidence))
    sections.append(build_s3_object_section(evidence))
    sections.append(build_s3_audit_section(evidence))
    sections.append(build_vendor_audit_section(evidence))

    # Grafana logs (before BetterStack, preserving original order)
    sections.append(build_grafana_logs_section(evidence))

    # BetterStack
    sections.append(build_betterstack_section(evidence))

    # Grafana traces, metrics, alert rules
    sections.append(build_grafana_spans_section(evidence))
    sections.append(build_grafana_metrics_section(evidence))
    sections.append(build_grafana_alert_rules_section(evidence))

    # Datadog evidence
    sections.append(build_datadog_pods_section(evidence))
    sections.append(build_datadog_logs_section(evidence))
    sections.append(build_datadog_monitors_section(evidence))
    sections.append(build_datadog_events_section(evidence))

    # Vercel
    vercel_deployment = evidence.get("vercel_deployment", {})
    vercel_failed_deployments = evidence.get("vercel_failed_deployments", [])
    if vercel_deployment or vercel_failed_deployments:
        sections.append(
            build_vercel_section(
                vercel_deployment=vercel_deployment,
                vercel_failed_deployments=vercel_failed_deployments,
                vercel_error_events=evidence.get("vercel_error_events", []),
                vercel_runtime_logs=evidence.get("vercel_runtime_logs", []),
                vercel_url=str(vercel_url or ""),
            )
        )

    # GitHub
    github_commits = evidence.get("github_commits", [])
    github_code_matches = evidence.get("github_code_matches", [])
    github_file = evidence.get("github_file", {})
    if github_commits or github_code_matches or github_file:
        sections.append(
            build_github_section(
                github_commits=github_commits,
                github_code_matches=github_code_matches,
                github_file=github_file,
            )
        )

    # CloudOpsBench
    sections.append(build_cloudopsbench_section(evidence))

    # OpenClaw conversation context
    sections.append(build_openclaw_section(evidence))

    # Alert annotations and raw alert text
    if alert_annotations:
        annotation_section = build_alert_annotations_section(alert_annotations)
        if annotation_section:
            sections.append(annotation_section)

    if raw_alert_text:
        sections.append(f"\nAlert Notification Text:\n{raw_alert_text[:2000]}\n")

    return "".join(s for s in sections if s)
