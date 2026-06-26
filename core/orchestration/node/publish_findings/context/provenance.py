"""Concrete source provenance summaries for report context."""

from __future__ import annotations

from typing import Any
from urllib.parse import urlparse

PROVENANCE_SOURCE_ALIASES: dict[str, str] = {
    "cloudwatch_logs": "cloudwatch",
    "grafana_logs": "grafana",
    "grafana_traces": "grafana",
    "datadog_logs": "datadog",
    "datadog_monitors": "datadog",
    "datadog_events": "datadog",
    "honeycomb_traces": "honeycomb",
    "coralogix_logs": "coralogix",
    "betterstack_logs": "betterstack",
    "s3_metadata": "s3",
    "s3_audit": "s3",
}


def _normalize_endpoint_target(endpoint: str) -> str:
    parsed = urlparse(endpoint.strip())
    return parsed.netloc or parsed.path.strip("/") or endpoint.strip()


def build_source_provenance(
    available_sources: dict[str, dict[str, Any]],
) -> dict[str, dict[str, str]]:
    """Return a compact provenance summary for concrete source instances."""
    provenance: dict[str, dict[str, str]] = {}

    grafana = available_sources.get("grafana") or {}
    grafana_endpoint = str(grafana.get("grafana_endpoint") or grafana.get("endpoint") or "").strip()
    if grafana_endpoint:
        provenance["grafana"] = {
            "label": "Grafana",
            "summary": ", ".join(
                part
                for part in [
                    f"instance={_normalize_endpoint_target(grafana_endpoint)}",
                    f"service={grafana.get('service_name')}"
                    if grafana.get("service_name")
                    else None,
                    f"pipeline={grafana.get('pipeline_name')}"
                    if grafana.get("pipeline_name")
                    else None,
                ]
                if part
            ),
        }

    datadog = available_sources.get("datadog") or {}
    if datadog:
        provenance["datadog"] = {
            "label": "Datadog",
            "summary": ", ".join(
                part
                for part in [
                    f"site={datadog.get('site', 'datadoghq.com')}",
                    f"query={datadog.get('default_query')}"
                    if datadog.get("default_query")
                    else None,
                    f"namespace={((datadog.get('kubernetes_context') or {}).get('namespace'))}"
                    if (datadog.get("kubernetes_context") or {}).get("namespace")
                    else None,
                ]
                if part
            ),
        }

    honeycomb = available_sources.get("honeycomb") or {}
    if honeycomb:
        provenance["honeycomb"] = {
            "label": "Honeycomb",
            "summary": ", ".join(
                part
                for part in [
                    f"dataset={honeycomb.get('dataset', '__all__')}",
                    f"service={honeycomb.get('service_name')}"
                    if honeycomb.get("service_name")
                    else None,
                    f"trace_id={honeycomb.get('trace_id')}" if honeycomb.get("trace_id") else None,
                ]
                if part
            ),
        }

    coralogix = available_sources.get("coralogix") or {}
    if coralogix:
        provenance["coralogix"] = {
            "label": "Coralogix",
            "summary": ", ".join(
                part
                for part in [
                    f"application={coralogix.get('application_name')}"
                    if coralogix.get("application_name")
                    else None,
                    f"subsystem={coralogix.get('subsystem_name')}"
                    if coralogix.get("subsystem_name")
                    else None,
                ]
                if part
            ),
        }

    eks = available_sources.get("eks") or {}
    if eks:
        provenance["eks"] = {
            "label": "AWS EKS",
            "summary": ", ".join(
                part
                for part in [
                    f"cluster={eks.get('cluster_name')}" if eks.get("cluster_name") else None,
                    f"namespace={eks.get('namespace')}" if eks.get("namespace") else None,
                    f"pod={eks.get('pod_name')}" if eks.get("pod_name") else None,
                    f"deployment={eks.get('deployment')}" if eks.get("deployment") else None,
                    f"region={eks.get('region')}" if eks.get("region") else None,
                ]
                if part
            ),
        }

    cloudwatch = available_sources.get("cloudwatch") or {}
    if cloudwatch:
        provenance["cloudwatch"] = {
            "label": "CloudWatch",
            "summary": ", ".join(
                part
                for part in [
                    f"log_group={cloudwatch.get('log_group')}"
                    if cloudwatch.get("log_group")
                    else None,
                    f"stream={cloudwatch.get('log_stream')}"
                    if cloudwatch.get("log_stream")
                    else None,
                    f"region={cloudwatch.get('region')}" if cloudwatch.get("region") else None,
                ]
                if part
            ),
        }

    s3 = available_sources.get("s3") or {}
    if s3:
        provenance["s3"] = {
            "label": "S3",
            "summary": ", ".join(
                part
                for part in [
                    f"bucket={s3.get('bucket')}" if s3.get("bucket") else None,
                    f"key={s3.get('key')}" if s3.get("key") else None,
                    f"prefix={s3.get('prefix')}" if s3.get("prefix") else None,
                ]
                if part
            ),
        }

    github = available_sources.get("github") or {}
    if github:
        provenance["github"] = {
            "label": "GitHub",
            "summary": ", ".join(
                part
                for part in [
                    f"repo={github.get('owner')}/{github.get('repo')}"
                    if github.get("owner") and github.get("repo")
                    else None,
                    f"ref={github.get('ref')}" if github.get("ref") else None,
                    f"sha={github.get('sha')}" if github.get("sha") else None,
                ]
                if part
            ),
        }

    gitlab = available_sources.get("gitlab") or {}
    if gitlab:
        provenance["gitlab"] = {
            "label": "GitLab",
            "summary": ", ".join(
                part
                for part in [
                    f"project={gitlab.get('project_id')}" if gitlab.get("project_id") else None,
                    f"ref={gitlab.get('ref_name')}" if gitlab.get("ref_name") else None,
                    f"mr={gitlab.get('merge_request_iid')}"
                    if gitlab.get("merge_request_iid")
                    else None,
                ]
                if part
            ),
        }

    vercel = available_sources.get("vercel") or {}
    if vercel:
        provenance["vercel"] = {
            "label": "Vercel",
            "summary": ", ".join(
                part
                for part in [
                    f"project={vercel.get('project_name') or vercel.get('project_slug') or vercel.get('project_id')}"
                    if (
                        vercel.get("project_name")
                        or vercel.get("project_slug")
                        or vercel.get("project_id")
                    )
                    else None,
                    f"deployment_id={vercel.get('deployment_id')}"
                    if vercel.get("deployment_id")
                    else None,
                    f"commit={vercel.get('github_commit_sha')}"
                    if vercel.get("github_commit_sha")
                    else None,
                ]
                if part
            ),
        }

    return {
        source: details
        for source, details in provenance.items()
        if (details.get("summary") or "").strip()
    }
