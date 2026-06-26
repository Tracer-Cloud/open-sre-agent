"""Build evidence catalogs and attach catalog IDs to claims."""

from __future__ import annotations

from typing import Any

from core.orchestration.node.publish_findings.context.normalize import (
    NormalizedState,
    as_snippet,
)
from core.orchestration.node.publish_findings.urls.aws import (
    build_datadog_logs_url,
    build_grafana_explore_url,
    build_s3_console_url,
)

SOURCE_ALIASES: dict[str, str] = {
    "cloudwatch": "cloudwatch_logs",
    "cloudwatch_log": "cloudwatch_logs",
    "cloudwatch_logs": "cloudwatch_logs",
    "grafana": "grafana_logs",
    "grafana_loki": "grafana_logs",
    "datadog": "datadog_logs",
    "honeycomb": "honeycomb_traces",
    "coralogix": "coralogix_logs",
    "betterstack": "betterstack_logs",
}


def _add_s3_metadata(
    evidence: dict[str, Any],
    region: str | None,
    catalog: dict[str, dict],
    source_to_id: dict[str, str],
) -> None:
    s3_obj = evidence.get("s3_object", {}) or {}
    bucket, key = s3_obj.get("bucket"), s3_obj.get("key")
    if not (bucket and key):
        return
    eid = "evidence/s3_metadata/landing"
    meta = s3_obj.get("metadata", {}) or {}
    catalog[eid] = {
        "label": "S3 Object Metadata",
        "url": build_s3_console_url(str(bucket), str(key), region or "us-east-1"),
        "summary": f"{bucket}/{key}",
        "snippet": as_snippet(
            f"schema_change_injected={meta.get('schema_change_injected')}, "
            f"schema_version={meta.get('schema_version')}"
        ),
    }
    source_to_id["s3_metadata"] = eid


def _add_s3_audit(
    evidence: dict[str, Any],
    catalog: dict[str, dict],
    source_to_id: dict[str, str],
) -> None:
    s3_audit = evidence.get("s3_audit_payload", {}) or {}
    if not (s3_audit.get("bucket") and s3_audit.get("key")):
        return
    eid = "evidence/s3_audit/main"
    catalog[eid] = {
        "label": "S3 Audit Payload",
        "summary": f"{s3_audit['bucket']}/{s3_audit['key']}",
        "snippet": as_snippet(str(s3_audit.get("content", "")) or None),
    }
    source_to_id["s3_audit"] = eid
    source_to_id.setdefault("vendor_audit", eid)


def _add_vendor_audit(
    evidence: dict[str, Any],
    catalog: dict[str, dict],
    source_to_id: dict[str, str],
) -> None:
    vendor_audit = evidence.get("vendor_audit_from_logs") or {}
    if not vendor_audit or "vendor_audit" in source_to_id:
        return
    eid = "evidence/vendor_audit/main"
    catalog[eid] = {
        "label": "Vendor Audit",
        "summary": "External vendor audit record",
        "snippet": None,
    }
    source_to_id["vendor_audit"] = eid


def _add_cloudwatch(
    cloudwatch_url: str | None,
    catalog: dict[str, dict],
    source_to_id: dict[str, str],
) -> None:
    if not cloudwatch_url:
        return
    eid = "evidence/cloudwatch/prefect"
    catalog[eid] = {
        "label": "CloudWatch Logs",
        "url": cloudwatch_url,
        "snippet": None,
    }
    source_to_id["cloudwatch_logs"] = eid


def _add_grafana_logs(
    evidence: dict[str, Any],
    grafana_endpoint: str | None,
    catalog: dict[str, dict],
    source_to_id: dict[str, str],
) -> None:
    grafana_logs = evidence.get("grafana_logs") or []
    grafana_error_logs = evidence.get("grafana_error_logs") or []
    if not (grafana_logs or grafana_error_logs):
        return
    grafana_query = evidence.get("grafana_logs_query") or ""
    grafana_service = evidence.get("grafana_logs_service") or ""
    summary_parts = [
        p
        for p in [
            grafana_service or None,
            f"{len(grafana_logs)} logs" if grafana_logs else None,
            f"{len(grafana_error_logs)} errors" if grafana_error_logs else None,
        ]
        if p
    ]
    eid = "evidence/grafana/loki"
    catalog[eid] = {
        "label": "Grafana Loki Logs",
        "url": build_grafana_explore_url(grafana_endpoint or "", grafana_query)
        if grafana_query
        else None,
        "summary": ", ".join(summary_parts) or None,
        "snippet": as_snippet(grafana_query) if grafana_query else None,
    }
    source_to_id["grafana_logs"] = eid


def _add_datadog_logs(
    evidence: dict[str, Any],
    datadog_site: str,
    catalog: dict[str, dict],
    source_to_id: dict[str, str],
) -> None:
    datadog_logs = evidence.get("datadog_logs") or []
    datadog_error_logs = evidence.get("datadog_error_logs") or []
    if not (datadog_logs or datadog_error_logs):
        return
    datadog_query = evidence.get("datadog_logs_query") or ""
    summary_parts = [
        p
        for p in [
            f"{len(datadog_logs)} logs" if datadog_logs else None,
            f"{len(datadog_error_logs)} errors" if datadog_error_logs else None,
        ]
        if p
    ]
    top_msg = next(
        (
            e.get("message", "").strip()
            for e in (datadog_error_logs or datadog_logs)
            if e.get("message")
        ),
        None,
    )
    eid = "evidence/datadog/logs"
    catalog[eid] = {
        "label": "Datadog Logs",
        "url": build_datadog_logs_url(datadog_query, datadog_site) if datadog_query else None,
        "summary": ", ".join(summary_parts) or None,
        "snippet": as_snippet(top_msg)
        if top_msg
        else (as_snippet(datadog_query) if datadog_query else None),
    }
    source_to_id["datadog_logs"] = eid


def _add_datadog_monitors(
    evidence: dict[str, Any],
    datadog_site: str,
    catalog: dict[str, dict],
    source_to_id: dict[str, str],
) -> None:
    datadog_monitors = evidence.get("datadog_monitors") or []
    if not datadog_monitors:
        return
    triggered = [
        m for m in datadog_monitors if m.get("overall_state") in ("Alert", "Warn", "No Data")
    ]
    label = (
        f"Datadog Monitors ({len(triggered)} triggered)"
        if triggered
        else f"Datadog Monitors ({len(datadog_monitors)})"
    )
    eid = "evidence/datadog/monitors"
    catalog[eid] = {
        "label": label,
        "url": f"https://app.{datadog_site}/monitors/manage",
        "summary": f"{len(datadog_monitors)} monitors",
        "snippet": as_snippet(", ".join(m.get("name", "") for m in datadog_monitors[:3])),
    }
    source_to_id["datadog_monitors"] = eid


def _add_datadog_events(
    evidence: dict[str, Any],
    datadog_site: str,
    catalog: dict[str, dict],
    source_to_id: dict[str, str],
) -> None:
    datadog_events = evidence.get("datadog_events") or []
    if not datadog_events:
        return
    eid = "evidence/datadog/events"
    catalog[eid] = {
        "label": f"Datadog Events ({len(datadog_events)})",
        "url": f"https://app.{datadog_site}/event/explorer",
        "summary": f"{len(datadog_events)} events",
        "snippet": as_snippet(datadog_events[0].get("title", "")),
    }
    source_to_id["datadog_events"] = eid


def _add_datadog_failed_pods(
    evidence: dict[str, Any],
    datadog_site: str,
    catalog: dict[str, dict],
    source_to_id: dict[str, str],
) -> None:
    dd_ns = evidence.get("datadog_kube_namespace")
    dd_container = evidence.get("datadog_container_name")
    raw_pods: list[dict] = evidence.get("datadog_failed_pods", [])
    if not raw_pods and evidence.get("datadog_pod_name"):
        raw_pods = [
            {
                "pod_name": evidence["datadog_pod_name"],
                "namespace": dd_ns,
                "container": dd_container,
            }
        ]

    for idx, pod in enumerate(raw_pods):
        pname = pod.get("pod_name") or pod.get("name")
        if not pname:
            continue
        pns = pod.get("namespace") or pod.get("kube_namespace") or dd_ns
        pcontainer = pod.get("container") or pod.get("container_name") or dd_container
        pod_query = f"kube_namespace:{pns} pod_name:{pname}" if pns else f"pod_name:{pname}"
        summary_parts = [f"namespace={pns}"] if pns else []
        if pod.get("exit_code") is not None:
            summary_parts.append(f"exit={pod['exit_code']}")
        if pod.get("memory_requested") and pod.get("memory_limit"):
            summary_parts.append(
                f"mem requested={pod['memory_requested']} limit={pod['memory_limit']}"
            )
        eid = f"evidence/datadog/failed_pod/{pname}"
        catalog[eid] = {
            "label": f"Failed Pod: {pname}{f' ({pcontainer})' if pcontainer else ''}",
            "url": build_datadog_logs_url(pod_query, datadog_site),
            "summary": ", ".join(summary_parts) if summary_parts else pname,
            "snippet": pod.get("error"),
        }
        if idx == 0:
            source_to_id["datadog_pod"] = eid


def _add_honeycomb_traces(
    evidence: dict[str, Any],
    catalog: dict[str, dict],
    source_to_id: dict[str, str],
) -> None:
    honeycomb_traces = evidence.get("honeycomb_traces") or []
    if not honeycomb_traces:
        return
    dataset = evidence.get("honeycomb_dataset") or "__all__"
    service_name = evidence.get("honeycomb_service_name") or ""
    trace_id = evidence.get("honeycomb_trace_id") or ""
    summary_parts = [
        part
        for part in [
            f"dataset={dataset}" if dataset else None,
            service_name or None,
            trace_id or None,
            f"{len(honeycomb_traces)} traces",
        ]
        if part
    ]
    eid = "evidence/honeycomb/traces"
    catalog[eid] = {
        "label": "Honeycomb Traces",
        "url": evidence.get("honeycomb_query_url") or None,
        "summary": ", ".join(summary_parts) or None,
        "snippet": None,
    }
    source_to_id["honeycomb_traces"] = eid


def _add_betterstack_logs(
    evidence: dict[str, Any],
    catalog: dict[str, dict],
    source_to_id: dict[str, str],
) -> None:
    betterstack_logs = evidence.get("betterstack_logs") or []
    if not betterstack_logs:
        return
    bs_source = str(evidence.get("betterstack_source") or "").strip()
    summary_parts = [
        part
        for part in [
            bs_source or None,
            f"{len(betterstack_logs)} rows" if betterstack_logs else None,
        ]
        if part
    ]
    # Better Stack stores the full log payload under the 'raw' column.
    top_raw = next(
        (
            str(entry.get("raw", "")).strip()
            for entry in betterstack_logs
            if isinstance(entry, dict) and entry.get("raw")
        ),
        None,
    )
    eid = "evidence/betterstack/logs"
    catalog[eid] = {
        "label": "Better Stack Logs",
        "summary": ", ".join(summary_parts) or None,
        "snippet": as_snippet(top_raw) if top_raw else None,
    }
    source_to_id["betterstack_logs"] = eid


def _add_coralogix_logs(
    evidence: dict[str, Any],
    catalog: dict[str, dict],
    source_to_id: dict[str, str],
) -> None:
    coralogix_logs = evidence.get("coralogix_logs") or []
    coralogix_error_logs = evidence.get("coralogix_error_logs") or []
    if not (coralogix_logs or coralogix_error_logs):
        return
    application_name = evidence.get("coralogix_application_name") or ""
    subsystem_name = evidence.get("coralogix_subsystem_name") or ""
    summary_parts = [
        part
        for part in [
            application_name or None,
            subsystem_name or None,
            f"{len(coralogix_logs)} logs" if coralogix_logs else None,
            f"{len(coralogix_error_logs)} errors" if coralogix_error_logs else None,
        ]
        if part
    ]
    top_msg = next(
        (
            entry.get("message", "").strip()
            for entry in (coralogix_error_logs or coralogix_logs)
            if entry.get("message")
        ),
        None,
    )
    eid = "evidence/coralogix/logs"
    catalog[eid] = {
        "label": "Coralogix Logs",
        "summary": ", ".join(summary_parts) or None,
        "snippet": as_snippet(top_msg)
        if top_msg
        else as_snippet(evidence.get("coralogix_logs_query")),
    }
    source_to_id["coralogix_logs"] = eid


def build_evidence_catalog(
    ns: NormalizedState,
) -> tuple[dict[str, dict], dict[str, str]]:
    """Build the full evidence catalog and the source-name to catalog-id index."""
    catalog: dict[str, dict] = {}
    source_to_id: dict[str, str] = {}

    _add_s3_metadata(ns.evidence, ns.cloudwatch_region, catalog, source_to_id)
    _add_s3_audit(ns.evidence, catalog, source_to_id)
    _add_vendor_audit(ns.evidence, catalog, source_to_id)
    _add_cloudwatch(ns.cloudwatch_url, catalog, source_to_id)
    _add_grafana_logs(ns.evidence, ns.grafana_endpoint, catalog, source_to_id)
    _add_datadog_logs(ns.evidence, ns.datadog_site, catalog, source_to_id)
    _add_datadog_monitors(ns.evidence, ns.datadog_site, catalog, source_to_id)
    _add_datadog_events(ns.evidence, ns.datadog_site, catalog, source_to_id)
    _add_datadog_failed_pods(ns.evidence, ns.datadog_site, catalog, source_to_id)
    _add_honeycomb_traces(ns.evidence, catalog, source_to_id)
    _add_coralogix_logs(ns.evidence, catalog, source_to_id)
    _add_betterstack_logs(ns.evidence, catalog, source_to_id)

    for i, entry in enumerate(catalog.values()):
        entry["display_id"] = f"E{i + 1}"

    return catalog, source_to_id


def attach_evidence_to_claims(
    claims: list[dict],
    source_to_id: dict[str, str],
    display_map: dict[str, str],
) -> list[dict]:
    """Return a copy of claims with evidence_ids, evidence_labels attached."""
    result: list[dict] = []
    for claim in claims:
        new_claim = dict(claim)
        evidence_ids: list[str] = []
        evidence_labels: list[str] = []
        for src in claim.get("evidence_sources", []) or []:
            key = SOURCE_ALIASES.get(src, src)
            if key == "evidence_analysis":
                continue
            eid = source_to_id.get(key)
            if eid and eid not in evidence_ids:
                evidence_ids.append(eid)
                evidence_labels.append(display_map.get(eid, eid))
        if evidence_ids:
            new_claim["evidence_ids"] = evidence_ids
            new_claim["evidence_labels"] = evidence_labels
        new_claim["evidence_sources"] = []
        result.append(new_claim)
    return result
