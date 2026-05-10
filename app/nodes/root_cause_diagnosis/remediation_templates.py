"""Deterministic remediation step fallbacks keyed on root_cause_category."""

from __future__ import annotations

from collections.abc import Mapping

_TEMPLATES: dict[str, list[tuple[str, str | None]]] = {
    "resource_exhaustion": [
        (
            "Identify the saturated resource (memory, CPU, connections, storage) from the evidence",
            None,
        ),
        ("Scale up or right-size the affected workload or database", None),
        ("Set resource limits and alerts at 80% to catch saturation early", None),
        ("Review Grafana dashboards for resource trend leading up to the incident", "grafana"),
        ("Check Datadog monitors for threshold breaches on the affected resource", "datadog"),
        ("List EKS pods and confirm OOMKill events with kubectl describe", "eks"),
    ],
    "connection_exhaustion": [
        ("Identify leaking clients and sessions consuming max_connections", None),
        ("Terminate idle-in-transaction sessions and recover headroom immediately", None),
        ("Enforce connection pool limits and idle session timeout policies", None),
        ("Review Grafana metrics for DatabaseConnections trend leading to exhaustion", "grafana"),
        ("Check Datadog monitors for connection saturation threshold breaches", "datadog"),
    ],
    "storage_exhaustion": [
        ("Confirm disk usage cliff and blocked-write symptoms from telemetry", None),
        ("Scale database storage or enable autoscaling before write traffic resumes", None),
        ("Throttle or segment bulk write jobs that consume storage too quickly", None),
        ("Review Grafana storage dashboards for FreeStorageSpace and WriteLatency", "grafana"),
        ("Check Datadog monitors for low-storage and write-latency threshold breaches", "datadog"),
    ],
    "cpu_saturation": [
        ("Identify top CPU-consuming query patterns in Performance Insights", None),
        ("Mitigate hot queries with indexing, query rewrite, or workload throttling", None),
        ("Scale compute only after query-level causes are addressed", None),
        ("Review Grafana CPU and query throughput metrics leading to saturation", "grafana"),
        ("Check Datadog monitors for sustained CPU saturation alerts", "datadog"),
    ],
    "replication_lag": [
        ("Quantify WAL generation versus replica replay capacity during incident window", None),
        ("Throttle or schedule write-heavy jobs to reduce replica replay backlog", None),
        ("Increase replica capacity or optimize replication path for sustained write bursts", None),
        ("Review Grafana ReplicaLag and TransactionLogsGeneration trends", "grafana"),
        ("Check Datadog monitors for replica lag threshold breaches", "datadog"),
    ],
    "checkpoint_io_storm": [
        ("Confirm checkpoint storm indicators and dominant LWLock I/O contention", None),
        ("Tune checkpoint/autovacuum settings and reduce bursty WAL flush pressure", None),
        ("Increase storage IOPS throughput while checkpoint pressure is stabilized", None),
        ("Review Grafana WriteIOPS and DiskQueueDepth spikes around the storm", "grafana"),
        ("Check Datadog monitors for disk queue and I/O latency alerts", "datadog"),
    ],
    "dual_resource_exhaustion": [
        ("Separate each independent resource bottleneck with its own evidence chain", None),
        ("Mitigate both bottlenecks in parallel to avoid recurrence under mixed workloads", None),
        ("Add independent alert thresholds for each constrained resource", None),
        ("Review Grafana dashboards for both constrained resources in the same window", "grafana"),
        ("Check Datadog monitors for concurrent multi-resource threshold breaches", "datadog"),
    ],
    "application_tier_load_spike": [
        ("Verify upstream application tier surge driving downstream database pressure", None),
        ("Rate-limit bursty application traffic and protect database concurrency limits", None),
        ("Right-size application tier autoscaling and backpressure controls", None),
        (
            "Review Grafana service-to-database traffic correlation in the incident window",
            "grafana",
        ),
        ("Check Datadog service-level monitors for traffic surge and error propagation", "datadog"),
    ],
    "dependency_failure": [
        ("Identify the failing upstream service or dependency from error logs", None),
        ("Check upstream service health page and recent deployments", None),
        ("Enable circuit breaker or retry with exponential backoff if not active", None),
        ("Review Grafana logs for connection errors or timeouts to the dependency", "grafana"),
        ("Check Datadog monitors for upstream SLO breach", "datadog"),
    ],
    "configuration_error": [
        (
            "Diff the configuration deployed before the incident against the last known-good config",
            None,
        ),
        ("Roll back the configuration change that introduced the mismatch", None),
        ("Add validation checks to CI/CD pipeline for configuration values", None),
    ],
    "code_defect": [
        (
            "Identify the commit introducing the defect using git history or recent deploy timestamps",
            None,
        ),
        ("Roll back or hot-fix the affected service", None),
        ("Add a regression test covering the failing code path before re-deploying", None),
    ],
    "data_quality": [
        ("Quarantine or skip the malformed records to unblock the pipeline", None),
        ("Add schema validation at the ingestion boundary", None),
        ("Trace the upstream source of the bad data and notify the owner", None),
    ],
    "infrastructure": [
        ("Check cloud provider status page and recent AWS service events for the region", None),
        ("Verify IAM roles, VPC security groups, and networking rules are unchanged", None),
        ("Trigger failover to standby if the primary zone is degraded", None),
    ],
    "unknown": [
        ("Enable debug logging and re-run the failing workload to gather more signal", None),
        (
            "Escalate to the owning team with the investigation trace and causal chain attached",
            None,
        ),
    ],
    "healthy": [],
}


def get_template_steps(category: str, available_sources: Mapping[str, object]) -> list[str]:
    """Return filtered remediation steps for the given root_cause_category."""
    entries = _TEMPLATES.get(category, _TEMPLATES["unknown"])
    return [
        step
        for step, required_source in entries
        if required_source is None or required_source in available_sources
    ]
