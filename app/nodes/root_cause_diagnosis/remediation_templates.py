"""Deterministic remediation step fallbacks keyed on root_cause_category.

The keys here mirror the taxonomy defined in
``app.types.root_cause_categories``. Categories without an explicit entry
fall through to the ``unknown`` template via :func:`get_template_steps`, so
adding a new category there does not require touching this module — but
adding a focused template here yields meaningfully better default
remediation copy for that specific failure mode.
"""

from __future__ import annotations

from collections.abc import Mapping

_TEMPLATES: dict[str, list[tuple[str, str | None]]] = {
    # ── Database — connection layer ───────────────────────────────────
    "connection_exhaustion": [
        ("Identify clients consuming max_connections via pg_stat_activity", None),
        ("Terminate idle-in-transaction sessions to recover headroom", None),
        ("Enforce server-side and pool-side connection caps with idle timeouts", None),
        ("Review Grafana DatabaseConnections trend leading to exhaustion", "grafana"),
        ("Check Datadog monitors for connection saturation breaches", "datadog"),
    ],
    "connection_pool_leak": [
        ("Locate the application code path that fails to release pool connections", None),
        ("Add a leaked-connection logger and a finite pool acquire timeout", None),
        ("Ship a hotfix that wraps the leaking acquire in a context manager / defer", None),
        ("Review Grafana pool checkout vs return rate during the incident window", "grafana"),
    ],
    "idle_in_transaction_session_leak": [
        (
            "Cancel sessions stuck `idle in transaction` longer than the SLA threshold",
            None,
        ),
        ("Set `idle_in_transaction_session_timeout` on the database server", None),
        ("Audit application code for missing COMMIT/ROLLBACK on early-return paths", None),
    ],
    # ── Database — compute layer ──────────────────────────────────────
    "cpu_saturation_bad_query": [
        ("Capture the dominant SQL pattern from Performance Insights and EXPLAIN it", None),
        ("Mitigate immediately by killing the offending session(s)", None),
        ("Add the missing index or rewrite the query to avoid sequential scans", None),
        ("Review Grafana CPU and ReadIOPS correlated with the query's db_load", "grafana"),
    ],
    "cpu_saturation_workload_burst": [
        ("Confirm aggregate workload increase rather than a single hot query", None),
        ("Throttle bursty callers and shift batch jobs out of peak windows", None),
        ("Right-size database compute or add a read replica for read traffic", None),
    ],
    "missing_index": [
        ("EXPLAIN the slow query and confirm Seq Scan on a high-cardinality column", None),
        ("Create the index CONCURRENTLY in production to avoid lock storms", None),
        ("Backfill statistics with ANALYZE and verify the planner picks the index", None),
    ],
    "query_plan_regression": [
        ("Compare current and previous plans via auto_explain or pg_stat_statements", None),
        ("Force the prior plan with planner hints / settings while root-cause is fixed", None),
        ("Refresh statistics and verify the new plan after stats update", None),
    ],
    "lock_contention": [
        ("Identify blocking session(s) via pg_locks / pg_stat_activity", None),
        ("Cancel long-running blockers; add lock_timeout to risky transactions", None),
        ("Restructure transactions to acquire locks in a consistent order", None),
    ],
    "deadlock_storm": [
        ("Inspect server logs for deadlock victims and the involved relations", None),
        ("Serialize conflicting writers or split hot rows to reduce contention", None),
        ("Add retries with exponential backoff on the application layer", None),
    ],
    # ── Database — storage / IO layer ─────────────────────────────────
    "storage_exhaustion": [
        ("Confirm disk full from FreeStorageSpace or RDS 'ran out of storage' events", None),
        ("Scale storage or enable autoscaling before write traffic resumes", None),
        ("Throttle / segment bulk write jobs that consume storage rapidly", None),
        ("Review Grafana FreeStorageSpace and WriteLatency around the incident", "grafana"),
    ],
    "storage_iops_throttling": [
        ("Identify the volume / instance class limit that is being hit", None),
        ("Move to a higher-IOPS volume type or provisioned-IOPS configuration", None),
        ("Reduce IOPS demand: cache hot reads, batch writes, vacuum bloated tables", None),
    ],
    "storage_burst_balance_depleted": [
        ("Confirm BurstBalance metric is at zero and baseline IOPS too low", None),
        ("Move from gp2 to gp3 with provisioned baseline IOPS / throughput", None),
        ("Smooth bursty workload to fit within the steady-state baseline", None),
    ],
    "checkpoint_io_storm": [
        ("Confirm dominant LWLock:BufferMapping wait events and WriteIOPS spike", None),
        ("Tune checkpoint_timeout / max_wal_size to spread flushes more evenly", None),
        ("Provision higher write IOPS while the storm is being stabilized", None),
        ("Review Grafana WriteIOPS and DiskQueueDepth spikes around the storm", "grafana"),
    ],
    "vacuum_freeze_storm": [
        ("Confirm autovacuum FREEZE on a large table is the dominant load source", None),
        ("Schedule manual VACUUM FREEZE during off-peak windows", None),
        ("Tune autovacuum_freeze_max_age and per-table autovacuum settings", None),
    ],
    "autovacuum_blocked": [
        ("Identify long-running transactions blocking autovacuum cleanup", None),
        ("Cancel offending transactions and tune statement_timeout", None),
        ("Tune autovacuum_naptime and per-table autovacuum thresholds", None),
    ],
    "transaction_id_wraparound_pressure": [
        ("Run an emergency VACUUM FREEZE on the highest-age tables", None),
        ("Cancel long-running transactions preventing freeze cleanup", None),
        ("Add monitoring on MaximumUsedTransactionIDs with paging thresholds", None),
    ],
    "table_bloat": [
        ("Quantify bloat with pgstattuple / pg_class on the suspect tables", None),
        ("Run pg_repack to reclaim space without long exclusive locks", None),
        ("Tune autovacuum aggressiveness for high-churn tables", None),
    ],
    # ── Database — replication ────────────────────────────────────────
    "replication_lag_wal_volume": [
        ("Quantify WAL generation versus replica replay capacity in the window", None),
        ("Throttle or schedule write-heavy jobs to reduce replay backlog", None),
        ("Increase replica capacity or optimize replication path for sustained writes", None),
        ("Review Grafana ReplicaLag and TransactionLogsGeneration trends", "grafana"),
    ],
    "replication_lag_long_query_on_replica": [
        ("Cancel the long-running replica query holding hot_standby_feedback", None),
        ("Move heavy reads to a dedicated read replica without query timeouts", None),
        ("Tune max_standby_streaming_delay and statement timeouts for analytics", None),
    ],
    "replication_lag_replica_undersized": [
        ("Right-size the replica instance class for steady-state write rate", None),
        ("Add additional replicas to spread analytical reads", None),
    ],
    "wal_archiving_failure": [
        ("Verify archive destination availability (S3 prefix / archive command)", None),
        ("Drain WAL backlog and confirm primary disk does not fill", None),
        ("Add monitoring for archive_status delays", None),
    ],
    "failover_event": [
        ("Confirm Multi-AZ / replica failover from RDS event timeline", None),
        ("Verify application-side connection retry behavior recovered cleanly", None),
        ("Document the failover RCA from the provider (health check failure, etc.)", None),
    ],
    "dual_resource_exhaustion": [
        ("Separate each independent bottleneck with its own evidence chain", None),
        ("Mitigate both bottlenecks in parallel to avoid recurrence", None),
        ("Add independent alert thresholds for each constrained resource", None),
        ("Review Grafana dashboards for both resources in the same window", "grafana"),
        ("Check Datadog monitors for concurrent multi-resource breaches", "datadog"),
    ],
    # ── Kubernetes / container workload ───────────────────────────────
    "pod_oomkilled": [
        ("Confirm OOMKill events via kubectl describe / kubelet logs", "eks"),
        ("Raise memory request/limit or fix the leak in the offending pod", None),
        ("Add memory headroom alerts at 80% of the limit", None),
    ],
    "pod_cpu_throttled": [
        ("Inspect container_cpu_cfs_throttled_seconds_total trend", None),
        ("Raise CPU limits or remove the limit if request-based scheduling suffices", None),
        ("Profile the workload and reduce CPU hotspots where feasible", None),
    ],
    "pod_evicted_node_pressure": [
        ("Identify which node pressure (memory/disk/PIDs) drove the eviction", None),
        ("Scale the node group or balance pod placement away from the hot node", None),
        ("Configure pod priorityClass to protect critical workloads", None),
    ],
    "pod_crashloop_backoff": [
        ("Pull the most recent crash log from the failing container", "eks"),
        ("Roll back the latest deploy if the crash started at a deploy timestamp", None),
        ("Add a startup probe for slow-init workloads to avoid spurious restarts", None),
    ],
    "pod_imagepull_backoff": [
        ("Confirm image tag exists and the pull secret is valid", None),
        ("Re-tag and re-deploy or restore the missing tag from the registry", None),
        ("Add registry availability monitoring", None),
    ],
    "pod_pending_insufficient_resources": [
        ("Inspect scheduler events for the insufficient-resource reason", None),
        ("Scale up the node group or right-size the pod requests", None),
    ],
    "pod_pending_unschedulable": [
        ("Inspect taints, affinity, and topology spread constraints", None),
        ("Adjust node taints/labels or relax pod placement rules", None),
    ],
    "node_not_ready": [
        ("Check kubelet, container runtime, and network plugin health on the node", None),
        ("Cordon and drain the node; replace if hardware/AMI level issue", None),
    ],
    "ingress_misconfiguration": [
        ("Validate ingress rules end-to-end (host, path, backend service/port)", None),
        ("Roll back the most recent ingress / certificate change", None),
    ],
    # ── Network / DNS ─────────────────────────────────────────────────
    "dns_resolution_failure": [
        ("Confirm resolution failures from CoreDNS / VPC Resolver logs", None),
        ("Roll back recent DNS changes; add NXDOMAIN/SERVFAIL alerts", None),
        ("Pin critical lookups to known-good resolvers temporarily", None),
    ],
    "tls_certificate_expired": [
        ("Identify the expired/expiring certificate from the failing handshake", None),
        ("Rotate the certificate and verify chain end-to-end", None),
        ("Add expiry monitoring at 30/14/7 days for all production certs", None),
    ],
    "load_balancer_unhealthy_targets": [
        ("Inspect target group health checks and the most recent failures", None),
        ("Fix backend health (rollback / scale) or relax health-check thresholds", None),
    ],
    "nat_gateway_throttling": [
        ("Confirm NAT gateway port allocation / throughput limit was hit", None),
        ("Add additional NAT gateways across AZs and rebalance routes", None),
    ],
    # ── Cloud storage ─────────────────────────────────────────────────
    "s3_object_missing": [
        ("Trace the upstream producer to confirm the object was never written", None),
        ("Re-run the producer step or adjust the consumer to handle late arrival", None),
    ],
    "s3_access_denied": [
        ("Diff the IAM policy / bucket policy against the last known-good revision", None),
        ("Restore the missing permission and re-run the failing job", None),
    ],
    # ── Dependency / external API ─────────────────────────────────────
    "upstream_service_outage": [
        ("Confirm the upstream incident from its status page or oncall channel", None),
        ("Enable circuit breaker / fallback path until upstream recovers", None),
        ("Add explicit alerting on upstream SLO breaches", None),
    ],
    "upstream_schema_change": [
        ("Diff the upstream contract against the consumer's expected schema", None),
        ("Roll forward a consumer fix or roll back the upstream change", None),
        ("Add contract tests at the integration boundary", None),
    ],
    "upstream_rate_limit": [
        ("Confirm 429 responses and the rate-limit window in upstream logs", None),
        ("Add jittered exponential backoff and request quota negotiation", None),
    ],
    "upstream_authentication_failure": [
        ("Verify token / cred has not rotated, expired, or been revoked", None),
        ("Rotate credentials, redeploy, and confirm 200s resume", None),
    ],
    # ── Code / configuration ──────────────────────────────────────────
    "bad_deploy": [
        (
            "Correlate the failure window with the most recent deploy timestamp",
            None,
        ),
        ("Roll back the offending deploy immediately", None),
        ("Add a regression test covering the failing path before re-deploying", None),
    ],
    "feature_flag_misconfiguration": [
        ("Identify the flag and the cohort it was enabled for", None),
        ("Disable the flag globally and confirm recovery", None),
        ("Add staged rollout guards for the flag going forward", None),
    ],
    "env_var_missing": [
        ("Identify which env var is missing from the failing service config", None),
        ("Restore the env var via the config store and redeploy", None),
    ],
    "env_var_misconfiguration": [
        ("Diff env var values against the last known-good config", None),
        ("Restore the correct value and redeploy", None),
    ],
    "secret_rotation_failure": [
        ("Identify the failing rotation step from the secret manager logs", None),
        ("Restore the previous secret version and re-attempt rotation", None),
    ],
    "code_defect_concurrency_bug": [
        ("Reproduce the race / lost-update under controlled concurrency", None),
        ("Add the necessary lock / idempotency guard and a regression test", None),
    ],
    "code_defect_resource_leak": [
        ("Pinpoint the leaking resource (FD, connection, memory) via heap/leak profile", None),
        ("Patch the leak path; add a finite resource cap to fail fast next time", None),
    ],
    # ── Data / pipeline ───────────────────────────────────────────────
    "data_schema_drift": [
        ("Diff producer and consumer schemas; identify the changed/added field", None),
        ("Quarantine drifted records and roll the consumer forward", None),
        ("Enforce schema validation at the ingestion boundary", None),
    ],
    "data_late_arrival": [
        ("Confirm the missing partition / batch is genuinely late vs lost", None),
        ("Re-run the upstream producer or extend the consumer's wait window", None),
    ],
    "lambda_concurrent_executions_exceeded": [
        ("Confirm the throttling reason from Lambda metrics and account quotas", None),
        ("Raise the concurrency reservation or partition workload across functions", None),
    ],
    # ── Workload / traffic ────────────────────────────────────────────
    "application_tier_load_spike": [
        ("Verify upstream application surge driving downstream pressure", None),
        ("Rate-limit bursty traffic and protect downstream concurrency limits", None),
        ("Right-size autoscaling and backpressure controls", None),
        (
            "Review Grafana service-to-database traffic correlation in the incident window",
            "grafana",
        ),
        (
            "Check Datadog service-level monitors for traffic surge and error propagation",
            "datadog",
        ),
    ],
    "traffic_burst_unprotected": [
        ("Confirm the spike size relative to autoscale headroom", None),
        ("Add edge rate-limits and queue-based load leveling", None),
    ],
    "ddos_event": [
        ("Engage WAF / shield rules to absorb or block the abusive pattern", None),
        ("Coordinate with the cloud provider's DDoS response team", None),
    ],
    "cascading_failure": [
        ("Identify the first service to degrade and break the propagation path", None),
        ("Add bulkheads and circuit breakers on the cross-service hops", None),
    ],
    # ── Infrastructure ────────────────────────────────────────────────
    "az_outage": [
        ("Confirm AZ scope from CloudWatch / status page and shift traffic away", None),
        ("Verify Multi-AZ failover behaved as designed", None),
    ],
    "iam_policy_misconfiguration": [
        ("Diff the IAM policy against the last known-good revision", None),
        ("Restore the missing permission and re-run the failing action", None),
    ],
    "service_quota_exceeded": [
        ("Confirm the breached quota from the cloud quota dashboard", None),
        ("Request a quota increase and add monitoring for headroom", None),
    ],
    # ── Generic fallbacks (kept for backward compatibility) ───────────
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
    """Return filtered remediation steps for the given root_cause_category.

    Categories absent from the template map fall through to the ``unknown``
    template so that adding a new entry to the taxonomy never silently
    breaks remediation rendering.
    """
    entries = _TEMPLATES.get(category, _TEMPLATES["unknown"])
    return [
        step
        for step, required_source in entries
        if required_source is None or required_source in available_sources
    ]
