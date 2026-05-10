"""LLM directive builders for root cause diagnosis prompts."""

from __future__ import annotations

from typing import Any

from app.state import InvestigationState


def _build_failover_directive(evidence: dict[str, Any]) -> str:
    rds_events = evidence.get("aws_rds_events", [])
    grafana_logs = evidence.get("grafana_logs", [])
    grafana_error_logs = evidence.get("grafana_error_logs", [])

    event_messages = " ".join(
        str(event.get("message", "")).lower() for event in rds_events if isinstance(event, dict)
    )

    log_messages = " ".join(
        str(log.get("message", "")).lower()
        for log in [*grafana_logs, *grafana_error_logs]
        if isinstance(log, dict)
    )

    has_rds_failover = "failover" in event_messages and (
        "multi-az" in event_messages
        or "health check failure" in event_messages
        or "primary host" in event_messages
    )

    has_log_failover = "failover" in log_messages and (
        "multi-az" in log_messages
        or "health check failure" in log_messages
        or "primary host" in log_messages
    )

    if not (has_rds_failover or has_log_failover):
        return ""

    return """
FAILOVER-SPECIFIC RULES:
- This incident is a failover scenario.
- You MUST use the exact phrase: "primary evidence source".
- You MUST include the exact phrases:
  - "failover initiated"
  - "failover in progress"
  - "failover completed"
  - "instance available"
  - "workload resumed normally"
- Do not paraphrase or omit any of these phrases.

REQUIRED FAILOVER OUTPUT:
- ROOT_CAUSE MUST include:
  "Based on the RDS event timeline (primary evidence source)"
- VALIDATED_CLAIMS MUST include one claim with this exact sequence:
  "failover initiated -> failover in progress -> failover completed -> instance available"
- CAUSAL_CHAIN MUST include:
  "health check failure -> failover -> standby promotion -> DNS update -> brief outage -> recovery"
- ROOT_CAUSE or VALIDATED_CLAIMS MUST explicitly say:
  "the system recovered and workload resumed normally"
"""


def _build_upstream_directive(evidence: dict[str, Any]) -> str:
    """Build upstream tracing directive if audit evidence is present."""
    s3_audit_payload = evidence.get("s3_audit_payload", {})
    vendor_audit_from_logs = evidence.get("vendor_audit_from_logs", {})

    if s3_audit_payload.get("found") or vendor_audit_from_logs:
        return """
**CRITICAL: Upstream Root Cause Tracing**
Audit evidence shows external API interactions. For upstream-triggered failures:
- The root cause is often upstream (external API schema changes, missing fields, breaking changes)
- S3 audit payload and vendor audit logs contain the source of truth
- Validated claims should reference the external API request/response details
- Explain how the external change propagated downstream to cause the pipeline failure
"""
    return ""


def _build_database_directive(state: InvestigationState, evidence: dict[str, Any]) -> str:
    """Build RDS / Database root cause disambiguation directive."""
    pipeline = str(state.get("pipeline", "")).lower()
    alert_text = (str(state.get("alert_name", "")) + " " + str(state.get("raw_alert", ""))).lower()
    is_database_incident = any(k in pipeline for k in ["rds", "postgres", "mysql"]) or any(
        k in alert_text for k in ["rds", "postgres", "mysql", "database", "db instance"]
    )

    has_db_evidence = bool(
        evidence.get("aws_rds_events")
        or evidence.get("aws_performance_insights")
        or bool(
            isinstance(evidence.get("aws_cloudwatch_metrics"), dict)
            and (
                evidence.get("aws_cloudwatch_metrics", {}).get("DBInstanceIdentifier")
                or evidence.get("aws_cloudwatch_metrics", {}).get("db_instance_identifier")
            )
        )
    )
    if not (has_db_evidence or is_database_incident):
        return ""
    return """
**CRITICAL: Database Resource Disambiguation (RDS / Postgres / MySQL)**
When evaluating database health metrics, pick the narrowest category from the database group of the taxonomy. Avoid the generic `resource_exhaustion` fallback — it is reserved for cases where no narrower category fits.

- Connection pool leaks (exhausting `max_connections`) → `connection_pool_leak` if the leak is on the application side, or `connection_exhaustion` when the server-side ceiling is hit and the application-side cause is not yet pinpointed. High CPU is often just a secondary symptom of accumulated idle sessions. If connections are near 100% AND CPU is near 100%, look for a single shared root cause (a pool leak holding open scan-heavy queries) — do NOT treat them as two independent problems.
- Sessions stuck `idle in transaction` while holding locks and slots → `idle_in_transaction_session_leak`.
- Storage exhaustion (`FreeStorageSpace` approaches 0; writes block; Write IOPS collapses to 0) → `storage_exhaustion`. If the `FreeStorageSpace` metric is missing, infer storage exhaustion from indirect signals: WriteIOPS dropping to 0, WriteLatency spiking, and RDS events indicating 'ran out of storage space'.
- IOPS or throughput limits reached on the volume (queues build, latency rises while free space is fine) → `storage_iops_throttling`. For burst-balance depletion on gp2-style volumes specifically → `storage_burst_balance_depleted`.
- A single bad query driving CPU near 100% while connections and storage are healthy → `cpu_saturation_bad_query`. Name the exact SQL statement from Performance Insights, cite its db_load / wait event if present, and explicitly rule out connection exhaustion when DatabaseConnections is stable. If a missing index is the root contributor → `missing_index`. If the planner switched to a worse plan after stats / parameter / version change → `query_plan_regression`.
- Aggregate workload increase saturates CPU without one dominant query → `cpu_saturation_workload_burst`.
- Checkpoint Storms / VACUUM FREEZE: If CPU is high but the dominant wait event is `LWLock:BufferMapping` with massive WriteIOPS, classify as `checkpoint_io_storm` (or `vacuum_freeze_storm` when an autovacuum FREEZE is the explicit driver), NOT `code_defect`. The high CPU is a downstream symptom of I/O contention.
- Replication lag: If write-heavy workload on the primary generates WAL faster than the replica can replay (ReplicaLag spikes) → `replication_lag_wal_volume`. If a long-running query on the replica is blocking apply → `replication_lag_long_query_on_replica`. If the replica instance class is steady-state too small → `replication_lag_replica_undersized`. Watch out for red herrings: if concurrent analytics queries cause high CPU, do NOT classify the root cause as CPU-driven when the failing metric (ReplicaLag) is driven by the write-heavy workload. Treat the CPU spike as an unrelated confounder/red herring and explicitly describe it as a red herring (i.e., not the root cause) in the final answer. Do not treat it as a second root cause or a contributing cause. If the `ReplicaLag` metric is missing, infer lag from RDS events (e.g., 'exceeded 900s') and high `TransactionLogsGeneration`.
- Compositional Faults: If two completely independent workloads cause two separate faults simultaneously (e.g., CPU saturation from an analytics SELECT AND storage exhaustion from an audit_log INSERT), explicitly identify BOTH as independent root causes (do not merge them into a single IOPS fault). You MUST explicitly state they are two independent, coincidental faults. Provide evidence for both the analytics query and the audit_log query. Use `dual_resource_exhaustion` as ROOT_CAUSE_CATEGORY and describe both causes clearly in ROOT_CAUSE (e.g., "Two independent root causes: ..."). Trace each causal chain separately in CAUSAL_CHAIN. You MUST explicitly state that connection growth is a symptom of blocked writers, not connection exhaustion. You MUST explicitly state that ReplicaLag growth is a downstream symptom of the write burst (not an independent fault). NEVER diagnose `connection_exhaustion` as a root cause when connections spike due to a blocked write queue.
- Misleading Context: Check RDS event timestamps carefully! Ignore historical events (maintenance, failovers, replica promotions) that completed hours before the current incident started.
- Healthy Systems / Stale Alerts: If metrics are oscillating but remain within normal operating bounds (e.g. connections at 55-65%, CPU at 40-70%, no error logs), the system is `healthy`. If a threshold was briefly crossed (e.g. low FreeStorageSpace) but autoscaling successfully expanded the volume and fully recovered the system before the investigation, the system is `healthy` and the alert is stale.

- Healthy System Detection:
If ALL of the following are true:
- connections are clearly below exhaustion levels or not proven to be near `max_connections`
- CPU is not near saturation and remains within normal range
- no errors are present in logs or events
- DB load and latency do not show sustained degradation

Then:
- You MUST classify the system as `healthy`
- You MUST conclude "no active failure"
- You MUST NOT default to `unknown` only because an exact threshold such as `max_connections` is unavailable

When the above healthy conditions are ALL met, apply these scoped prohibitions:
- Do NOT infer connection pool leaks
- Do NOT infer resource exhaustion
- Do NOT generate speculative root causes
- Do NOT interpret monotonic increase or oscillation as failure

When the above healthy conditions are ALL met, apply these scoped interpretation rules:
- Trend ≠ failure
- Oscillation ≠ instability
- Moderate utilization ≠ degradation
- Missing exact thresholds do NOT imply failure or `unknown` when the available evidence shows no errors, moderate load, and no saturation signals

If an alert fires without errors or threshold breaches:
- treat it as a noisy or warning-level alert, NOT a real incident

- ALWAYS trace the causal chain properly (e.g., connection leak -> idle sessions -> connections maxed out, OR missing index -> full table scans -> ReadIOPS -> CPU saturated, OR VACUUM FREEZE -> massive WAL -> checkpoint flush -> I/O saturation -> CPU as symptom).
"""


def _extract_k8s_tags_from_evidence(evidence: dict[str, Any]) -> dict[str, str]:
    """Extract K8s metadata from Datadog log tags (Signal 1 -- primary).

    Also picks up pod_name and container_name alongside kube_* tags.
    Prefers pre-extracted values from fetch_datadog_context when available.
    """
    k8s: dict[str, str] = {}

    if evidence.get("datadog_pod_name"):
        k8s["pod_name"] = evidence["datadog_pod_name"]
    if evidence.get("datadog_container_name"):
        k8s["container_name"] = evidence["datadog_container_name"]
    if evidence.get("datadog_kube_namespace"):
        k8s["kube_namespace"] = evidence["datadog_kube_namespace"]

    for log_list_key in ("datadog_error_logs", "datadog_logs"):
        for log in evidence.get(log_list_key, []):
            if not isinstance(log, dict):
                continue
            for tag in log.get("tags", []):
                if not isinstance(tag, str) or ":" not in tag:
                    continue
                key, _, val = tag.partition(":")
                if (
                    key.startswith("kube_") or key in ("pod_name", "container_name")
                ) and key not in k8s:
                    k8s[key] = val
    return k8s


def _detect_k8s_from_monitors(evidence: dict[str, Any]) -> bool:
    """Check Datadog monitors for kubernetes_state queries (Signal 2 -- secondary)."""
    for mon in evidence.get("datadog_monitors", []):
        if isinstance(mon, dict) and "kubernetes_state" in mon.get("query", ""):
            return True
    return False


def _build_kubernetes_directive(state: InvestigationState, evidence: dict[str, Any]) -> str:
    """Build K8s diagnostic directive when Kubernetes context is detected.

    Detection cascade (strict priority):
    1. Tags: kube_* tags in Datadog log entries (most reliable)
    2. Monitors: kubernetes_state in Datadog monitor queries
    3. Alert text: "kubernetes" in raw alert (fallback only)
    """
    k8s_tags = _extract_k8s_tags_from_evidence(evidence)

    if not k8s_tags and not _detect_k8s_from_monitors(evidence):
        raw_alert = state.get("raw_alert", "")
        if "kubernetes" not in str(raw_alert).lower():
            return ""

    metadata_lines = [f"- {k}: {v}" for k, v in k8s_tags.items()] if k8s_tags else []
    metadata_block = "\n".join(metadata_lines)
    metadata_section = f"\n{metadata_block}\n" if metadata_block else ""

    return f"""
**Kubernetes Context Detected:**{metadata_section}
For Kubernetes workload failures, distinguish clearly between:
- SYMPTOM: The workload (Job/Pod/Deployment) entered a failed state
- ROOT CAUSE: The underlying reason the application process failed

Common root cause categories for K8s workload failures:
- Configuration error: environment variables, ConfigMaps, or manifest settings that cause the application to reject valid input or fail validation
- Resource issue: OOM, CPU throttling, storage limits
- Image/dependency: wrong image tag, missing dependencies, startup crash
- Input data: upstream data quality or missing data

Key distinction for schema/validation failures:
When logs show the application validating data against a configured list of required fields, and a field in that list is missing from the data, the root cause is typically a configuration error (the required fields list was misconfigured in the Job manifest or environment variables), not a data quality issue. Data quality issues manifest as malformed values or corrupted records, not as a mismatch between a configured field list and the data schema.

Identify which category fits the evidence and trace the error chain from configuration/input through to the observed failure.
"""


def _build_memory_section(memory_context: str) -> str:
    """Build memory section if context is available."""
    if not memory_context:
        return ""

    return f"""
**Prior Root Cause Patterns (from memory):**
{memory_context[:1500]}

Use these patterns to recognize similar failure modes and accelerate diagnosis.
"""
