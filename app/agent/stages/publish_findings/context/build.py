"""Assemble formatter context from investigation state."""

from __future__ import annotations

from app.agent.stages.publish_findings.context.evidence_catalog import (
    attach_evidence_to_claims,
    build_evidence_catalog,
)
from app.agent.stages.publish_findings.context.normalize import NormalizedState, safe_get
from app.agent.stages.publish_findings.context.provenance import (
    PROVENANCE_SOURCE_ALIASES,
    build_source_provenance,
)
from app.agent.stages.publish_findings.context.schema import ReportContext
from app.state import InvestigationState


def build_report_context(state: InvestigationState) -> ReportContext:
    """Build the full ReportContext from an InvestigationState."""
    ns = NormalizedState(state)
    source_provenance = build_source_provenance(ns.available_sources)
    catalog, source_to_id = build_evidence_catalog(ns)

    # Add provenance summaries to evidence entries when possible.
    for source_name, entry_id in source_to_id.items():
        provenance_key = PROVENANCE_SOURCE_ALIASES.get(source_name, source_name)
        if provenance_key in source_provenance and entry_id in catalog:
            catalog[entry_id]["provenance"] = source_provenance[provenance_key]["summary"]

    display_map = {eid: entry.get("display_id", eid) for eid, entry in catalog.items()}
    validated_claims = attach_evidence_to_claims(ns.validated_claims, source_to_id, display_map)
    non_validated_claims = attach_evidence_to_claims(
        ns.non_validated_claims, source_to_id, display_map
    )

    return {
        # Core RCA results
        "pipeline_name": state.get("pipeline_name", "unknown"),
        "alert_name": state.get("alert_name"),
        "root_cause": state.get("root_cause", ""),
        "root_cause_category": state.get("root_cause_category"),
        "validated_claims": validated_claims,
        "non_validated_claims": non_validated_claims,
        "validity_score": state.get("validity_score", 0.0),
        "investigation_recommendations": state.get("investigation_recommendations", []),
        "remediation_steps": state.get("remediation_steps", []),
        "correlation": state.get("correlation", {}),
        # S3 verification
        "s3_marker_exists": ns.s3.get("marker_exists", False),
        # Tracer web run metadata
        "tracer_run_status": ns.web_run.get("status"),
        "tracer_run_name": ns.web_run.get("run_name"),
        "tracer_pipeline_name": ns.web_run.get("pipeline_name"),
        "tracer_run_cost": ns.web_run.get("run_cost", 0),
        "tracer_max_ram_gb": ns.web_run.get("max_ram_gb", 0),
        "tracer_user_email": ns.web_run.get("user_email"),
        "tracer_team": ns.web_run.get("team"),
        "tracer_instance_type": ns.web_run.get("instance_type"),
        "tracer_failed_tasks": len(ns.evidence.get("failed_jobs", [])),
        # AWS Batch metadata
        "batch_failure_reason": ns.batch.get("failure_reason"),
        "batch_failed_jobs": ns.batch.get("failed_jobs", 0),
        # CloudWatch metadata
        "cloudwatch_log_group": ns.cloudwatch_group,
        "cloudwatch_log_stream": ns.cloudwatch_stream,
        "cloudwatch_logs_url": ns.cloudwatch_url,
        "cloudwatch_region": ns.cloudwatch_region,
        "alert_id": ns.alert_id,
        "evidence_catalog": catalog,
        "investigation_duration_seconds": ns.duration_seconds,
        # Raw data for deeper inspection
        "evidence": ns.evidence,
        "raw_alert": ns.raw_alert,
        # Tool call history for investigation transparency
        "executed_hypotheses": state.get("executed_hypotheses", []),
        "evidence_entries": state.get("evidence_entries", []),
        # Integration endpoints for deep links
        "grafana_endpoint": ns.grafana_endpoint,
        "datadog_site": ns.datadog_site,
        "source_provenance": source_provenance,
        "severity": (state.get("severity") or None),
        # Kubernetes pod details: from Datadog evidence first, then alert annotations.
        "kube_pod_name": (
            ns.evidence.get("datadog_pod_name")
            or safe_get(ns.raw_alert, "annotations", "hostname")
            or safe_get(ns.raw_alert, "commonAnnotations", "hostname")
        ),
        "kube_container_name": (
            ns.evidence.get("datadog_container_name")
            or safe_get(ns.raw_alert, "annotations", "container_name")
            or safe_get(ns.raw_alert, "commonAnnotations", "container_name")
        ),
        "kube_namespace": (
            ns.evidence.get("datadog_kube_namespace")
            or safe_get(ns.raw_alert, "annotations", "namespace")
            or safe_get(ns.raw_alert, "commonAnnotations", "namespace")
            or safe_get(ns.raw_alert, "annotations", "kube_namespace")
        ),
        # Multiple failed pods: populated from Datadog evidence when available.
        "kube_failed_pods": ns.evidence.get("datadog_failed_pods", []),
    }
