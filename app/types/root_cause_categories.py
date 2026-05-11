"""Root cause taxonomy for incident diagnosis.

Single source of truth for ``ROOT_CAUSE_CATEGORY`` values emitted by the
diagnosis node and validated by the response parser.

Why this lives in ``app/types`` and not in ``app/services/llm_client.py`` or
``app/nodes/root_cause_diagnosis/``:

- The LLM transport layer must not own domain taxonomies; it ships strings.
- The diagnosis node consumes the taxonomy but should not own it either —
  the prompt builder, the parser, and downstream renderers all need it.
- ``app/types`` is the canonical home for shared, dependency-free contracts.

A category is intentionally narrow. Operators investigating an incident
need a label that points directly at the failing subsystem ("storage IOPS
throttling", "WAL replay backlog", "OOMKilled") rather than a coarse
bucket ("resource_exhaustion") that matches a dozen unrelated failure
modes.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final


@dataclass(frozen=True)
class RootCauseCategory:
    """One taxonomy entry: stable ``name``, ``group`` for grouping in prompts/UI,
    ``description`` for prompt and operator-facing rendering."""

    name: str
    group: str
    description: str


GROUP_DATABASE: Final[str] = "database"
GROUP_KUBERNETES: Final[str] = "kubernetes_workload"
GROUP_NETWORK: Final[str] = "network_and_dns"
GROUP_CLOUD_STORAGE: Final[str] = "cloud_storage"
GROUP_DEPENDENCY: Final[str] = "external_dependency"
GROUP_CODE_AND_CONFIG: Final[str] = "code_and_configuration"
GROUP_DATA_PIPELINE: Final[str] = "data_and_pipeline"
GROUP_WORKLOAD: Final[str] = "workload_and_traffic"
GROUP_INFRASTRUCTURE: Final[str] = "infrastructure"
GROUP_GENERIC: Final[str] = "generic_fallback"


_GROUP_ORDER: tuple[str, ...] = (
    GROUP_DATABASE,
    GROUP_KUBERNETES,
    GROUP_NETWORK,
    GROUP_CLOUD_STORAGE,
    GROUP_DEPENDENCY,
    GROUP_CODE_AND_CONFIG,
    GROUP_DATA_PIPELINE,
    GROUP_WORKLOAD,
    GROUP_INFRASTRUCTURE,
    GROUP_GENERIC,
)


_TAXONOMY: tuple[RootCauseCategory, ...] = (
    # ── Database — connection layer ────────────────────────────────────
    RootCauseCategory(
        "connection_exhaustion",
        GROUP_DATABASE,
        "max_connections ceiling reached; new sessions are rejected.",
    ),
    RootCauseCategory(
        "connection_pool_leak",
        GROUP_DATABASE,
        "Application connection pool acquires faster than it releases.",
    ),
    RootCauseCategory(
        "idle_in_transaction_session_leak",
        GROUP_DATABASE,
        "Sessions stuck idle-in-transaction hold locks and slots.",
    ),
    RootCauseCategory(
        "max_connections_misconfigured",
        GROUP_DATABASE,
        "Server-side max_connections value is too low for current load shape.",
    ),
    # ── Database — compute layer ───────────────────────────────────────
    RootCauseCategory(
        "cpu_saturation_bad_query",
        GROUP_DATABASE,
        "One hot SQL pattern (missing index, full scan) saturates CPU.",
    ),
    RootCauseCategory(
        "cpu_saturation_workload_burst",
        GROUP_DATABASE,
        "Aggregate workload increase saturates CPU without one dominant query.",
    ),
    RootCauseCategory(
        "query_plan_regression",
        GROUP_DATABASE,
        "Planner choice changed (stats, parameter, version) and degraded.",
    ),
    RootCauseCategory(
        "stale_statistics",
        GROUP_DATABASE,
        "Outdated planner stats produce inefficient plans on changed data.",
    ),
    RootCauseCategory(
        "missing_index",
        GROUP_DATABASE,
        "Required selective index absent; sequential scans dominate IO/CPU.",
    ),
    RootCauseCategory(
        "lock_contention",
        GROUP_DATABASE,
        "Row/table locks queue; queries wait without saturating compute.",
    ),
    RootCauseCategory(
        "deadlock_storm",
        GROUP_DATABASE,
        "Repeated deadlock cycles drive transaction failure rate.",
    ),
    # ── Database — storage / IO layer ──────────────────────────────────
    RootCauseCategory(
        "storage_exhaustion",
        GROUP_DATABASE,
        "Disk full; writes block, FreeStorageSpace approaches zero.",
    ),
    RootCauseCategory(
        "storage_iops_throttling",
        GROUP_DATABASE,
        "EBS/storage IOPS or throughput limit reached, queueing reads/writes.",
    ),
    RootCauseCategory(
        "storage_burst_balance_depleted",
        GROUP_DATABASE,
        "gp2-style burst balance exhausted; baseline IOPS too low for load.",
    ),
    RootCauseCategory(
        "checkpoint_io_storm",
        GROUP_DATABASE,
        "Bursty checkpoint flushes saturate write I/O (LWLock:BufferMapping).",
    ),
    RootCauseCategory(
        "vacuum_freeze_storm",
        GROUP_DATABASE,
        "Autovacuum FREEZE on a large table dominates load and locks.",
    ),
    RootCauseCategory(
        "autovacuum_blocked",
        GROUP_DATABASE,
        "Autovacuum cannot keep up due to long transactions or settings.",
    ),
    RootCauseCategory(
        "transaction_id_wraparound_pressure",
        GROUP_DATABASE,
        "MaximumUsedTransactionIDs nearing wraparound; reads/writes degrade.",
    ),
    RootCauseCategory(
        "table_bloat",
        GROUP_DATABASE,
        "Heavy update/delete bloat degrades scans and inflates IO.",
    ),
    # ── Database — replication ─────────────────────────────────────────
    RootCauseCategory(
        "replication_lag_wal_volume",
        GROUP_DATABASE,
        "Primary generates WAL faster than replica can replay.",
    ),
    RootCauseCategory(
        "replication_lag_long_query_on_replica",
        GROUP_DATABASE,
        "Long-running query on replica blocks WAL apply.",
    ),
    RootCauseCategory(
        "replication_lag_replica_undersized",
        GROUP_DATABASE,
        "Replica instance class too small to keep up with steady write rate.",
    ),
    RootCauseCategory(
        "wal_archiving_failure",
        GROUP_DATABASE,
        "WAL archive backlog grows; threatens primary availability.",
    ),
    RootCauseCategory(
        "failover_event",
        GROUP_DATABASE,
        "Multi-AZ or replica failover; outage explained by topology change.",
    ),
    # ── Database — composite ───────────────────────────────────────────
    RootCauseCategory(
        "dual_resource_exhaustion",
        GROUP_DATABASE,
        "Two independent DB resources saturate simultaneously (compositional).",
    ),
    # ── Kubernetes / container workload ────────────────────────────────
    RootCauseCategory(
        "pod_oomkilled",
        GROUP_KUBERNETES,
        "Container exceeded memory limit and was killed by the kubelet.",
    ),
    RootCauseCategory(
        "pod_cpu_throttled",
        GROUP_KUBERNETES,
        "Container hit CPU limit and is throttled; latency rises.",
    ),
    RootCauseCategory(
        "pod_evicted_node_pressure",
        GROUP_KUBERNETES,
        "Pod evicted due to node memory/disk/PID pressure.",
    ),
    RootCauseCategory(
        "pod_crashloop_backoff",
        GROUP_KUBERNETES,
        "Container exits non-zero on start; kubelet backs off restarts.",
    ),
    RootCauseCategory(
        "pod_imagepull_backoff",
        GROUP_KUBERNETES,
        "Image pull fails (auth, missing tag, registry outage).",
    ),
    RootCauseCategory(
        "pod_pending_insufficient_resources",
        GROUP_KUBERNETES,
        "Scheduler cannot place pod due to CPU/mem/quota constraints.",
    ),
    RootCauseCategory(
        "pod_pending_unschedulable",
        GROUP_KUBERNETES,
        "Scheduler cannot place pod due to taints/affinity/topology.",
    ),
    RootCauseCategory(
        "liveness_probe_misconfigured",
        GROUP_KUBERNETES,
        "Liveness probe kills otherwise-healthy containers.",
    ),
    RootCauseCategory(
        "readiness_probe_misconfigured",
        GROUP_KUBERNETES,
        "Readiness probe failure removes pods from Service endpoints.",
    ),
    RootCauseCategory(
        "node_not_ready",
        GROUP_KUBERNETES,
        "Node went NotReady (kubelet, container runtime, network).",
    ),
    RootCauseCategory(
        "service_endpoint_mismatch",
        GROUP_KUBERNETES,
        "Service selector / target port doesn't match pod labels/ports.",
    ),
    RootCauseCategory(
        "deployment_rollout_stuck",
        GROUP_KUBERNETES,
        "Rollout blocked before any new pod becomes ready.",
    ),
    RootCauseCategory(
        "hpa_misconfiguration",
        GROUP_KUBERNETES,
        "HPA cannot scale (missing metrics, wrong target).",
    ),
    RootCauseCategory(
        "pdb_blocking_drain",
        GROUP_KUBERNETES,
        "PodDisruptionBudget prevents node drain or scale-in.",
    ),
    RootCauseCategory(
        "ingress_misconfiguration",
        GROUP_KUBERNETES,
        "Ingress/route rules don't match desired traffic path.",
    ),
    # ── Network / DNS ──────────────────────────────────────────────────
    RootCauseCategory(
        "dns_resolution_failure",
        GROUP_NETWORK,
        "Service or upstream DNS resolution fails or times out.",
    ),
    RootCauseCategory(
        "tls_certificate_expired",
        GROUP_NETWORK,
        "Certificate expiry or rotation issue breaks TLS handshakes.",
    ),
    RootCauseCategory(
        "mtls_handshake_failure",
        GROUP_NETWORK,
        "Mutual TLS misconfiguration prevents handshake completion.",
    ),
    RootCauseCategory(
        "security_group_misconfiguration",
        GROUP_NETWORK,
        "VPC/SG/NACL rule blocks required traffic.",
    ),
    RootCauseCategory(
        "load_balancer_unhealthy_targets",
        GROUP_NETWORK,
        "ALB/NLB/ELB target group health check failures drive traffic loss.",
    ),
    RootCauseCategory(
        "nat_gateway_throttling",
        GROUP_NETWORK,
        "NAT gateway port allocation or throughput limit reached.",
    ),
    RootCauseCategory(
        "network_partition",
        GROUP_NETWORK,
        "Connectivity loss between subnets, VPCs, or regions.",
    ),
    # ── Cloud storage / object stores ──────────────────────────────────
    RootCauseCategory(
        "s3_object_missing",
        GROUP_CLOUD_STORAGE,
        "Expected S3 object missing (timing, prefix, or upstream skipped).",
    ),
    RootCauseCategory(
        "s3_access_denied",
        GROUP_CLOUD_STORAGE,
        "S3 permissions/IAM policy block expected reads or writes.",
    ),
    RootCauseCategory(
        "s3_throttling",
        GROUP_CLOUD_STORAGE,
        "S3 prefix throttling (503 SlowDown) limits throughput.",
    ),
    RootCauseCategory(
        "ebs_volume_full",
        GROUP_CLOUD_STORAGE,
        "Attached EBS volume reached capacity; writes fail.",
    ),
    # ── External dependency / API ──────────────────────────────────────
    RootCauseCategory(
        "upstream_service_outage",
        GROUP_DEPENDENCY,
        "External service is unavailable / SLO breached.",
    ),
    RootCauseCategory(
        "upstream_schema_change",
        GROUP_DEPENDENCY,
        "Upstream API contract changed and broke consumers.",
    ),
    RootCauseCategory(
        "upstream_rate_limit",
        GROUP_DEPENDENCY,
        "Upstream throttling (HTTP 429) is the proximate cause.",
    ),
    RootCauseCategory(
        "upstream_authentication_failure",
        GROUP_DEPENDENCY,
        "Token / cred rotation / expiry breaks downstream auth.",
    ),
    RootCauseCategory(
        "third_party_breaking_change",
        GROUP_DEPENDENCY,
        "Third-party SDK or service made a backward-incompatible change.",
    ),
    # ── Code / configuration ───────────────────────────────────────────
    RootCauseCategory(
        "bad_deploy",
        GROUP_CODE_AND_CONFIG,
        "A specific deploy introduced the failure; rollback would restore.",
    ),
    RootCauseCategory(
        "feature_flag_misconfiguration",
        GROUP_CODE_AND_CONFIG,
        "Flag enables a broken path in the wrong env or cohort.",
    ),
    RootCauseCategory(
        "env_var_missing",
        GROUP_CODE_AND_CONFIG,
        "Required env var not set; service fails on startup or first use.",
    ),
    RootCauseCategory(
        "env_var_misconfiguration",
        GROUP_CODE_AND_CONFIG,
        "Env var value points at wrong endpoint / region / credential.",
    ),
    RootCauseCategory(
        "secret_missing",
        GROUP_CODE_AND_CONFIG,
        "Required secret not mounted/available to the workload.",
    ),
    RootCauseCategory(
        "secret_rotation_failure",
        GROUP_CODE_AND_CONFIG,
        "Rotation produced stale or invalid secrets.",
    ),
    RootCauseCategory(
        "code_defect_null_handling",
        GROUP_CODE_AND_CONFIG,
        "Unhandled null/None path breaks under specific input shape.",
    ),
    RootCauseCategory(
        "code_defect_concurrency_bug",
        GROUP_CODE_AND_CONFIG,
        "Race / lost-update / deadlock surfaces under concurrent load.",
    ),
    RootCauseCategory(
        "code_defect_resource_leak",
        GROUP_CODE_AND_CONFIG,
        "FD / connection / memory leak grows until exhaustion.",
    ),
    RootCauseCategory(
        "code_defect_serialization",
        GROUP_CODE_AND_CONFIG,
        "Encoder/decoder bug corrupts payloads or fails parse.",
    ),
    # ── Data / pipeline orchestration ──────────────────────────────────
    RootCauseCategory(
        "data_schema_drift",
        GROUP_DATA_PIPELINE,
        "Upstream schema changed; downstream consumers reject new shape.",
    ),
    RootCauseCategory(
        "data_missing_required_field",
        GROUP_DATA_PIPELINE,
        "Records missing a field declared as required by the consumer.",
    ),
    RootCauseCategory(
        "data_corrupted_record",
        GROUP_DATA_PIPELINE,
        "Malformed bytes/values cause parse errors mid-pipeline.",
    ),
    RootCauseCategory(
        "data_late_arrival",
        GROUP_DATA_PIPELINE,
        "Expected partition / batch never arrived in time window.",
    ),
    RootCauseCategory(
        "data_volume_anomaly",
        GROUP_DATA_PIPELINE,
        "Surprise volume change (zero or huge) trips downstream limits.",
    ),
    RootCauseCategory(
        "data_partition_skew",
        GROUP_DATA_PIPELINE,
        "One partition holds disproportionate volume; hot-spots workers.",
    ),
    RootCauseCategory(
        "job_timeout",
        GROUP_DATA_PIPELINE,
        "Job exceeded scheduler / runtime timeout.",
    ),
    RootCauseCategory(
        "job_dependency_failure",
        GROUP_DATA_PIPELINE,
        "Upstream job failed; downstream cannot proceed.",
    ),
    RootCauseCategory(
        "lambda_concurrent_executions_exceeded",
        GROUP_DATA_PIPELINE,
        "Lambda hit account/function concurrency limit; invocations throttled.",
    ),
    RootCauseCategory(
        "lambda_init_timeout",
        GROUP_DATA_PIPELINE,
        "Cold start / init phase exceeded timeout.",
    ),
    # ── Workload / traffic ─────────────────────────────────────────────
    RootCauseCategory(
        "application_tier_load_spike",
        GROUP_WORKLOAD,
        "Application tier traffic surge propagates pressure downstream.",
    ),
    RootCauseCategory(
        "traffic_burst_unprotected",
        GROUP_WORKLOAD,
        "Real traffic spike exceeds rate limits / autoscale headroom.",
    ),
    RootCauseCategory(
        "abusive_traffic",
        GROUP_WORKLOAD,
        "Bot or scraper traffic dominates capacity.",
    ),
    RootCauseCategory(
        "ddos_event",
        GROUP_WORKLOAD,
        "Distributed denial-of-service traffic pattern is the proximate cause.",
    ),
    RootCauseCategory(
        "cascading_failure",
        GROUP_WORKLOAD,
        "Backpressure between services cascades into broader degradation.",
    ),
    # ── Infrastructure / cloud platform ────────────────────────────────
    RootCauseCategory(
        "az_outage",
        GROUP_INFRASTRUCTURE,
        "Single availability zone degraded; symptoms scoped to that AZ.",
    ),
    RootCauseCategory(
        "region_outage",
        GROUP_INFRASTRUCTURE,
        "Region-wide cloud provider event.",
    ),
    RootCauseCategory(
        "cloud_provider_event",
        GROUP_INFRASTRUCTURE,
        "Confirmed provider service event published on the status page.",
    ),
    RootCauseCategory(
        "iam_policy_misconfiguration",
        GROUP_INFRASTRUCTURE,
        "IAM/role/policy denies an action the workload requires.",
    ),
    RootCauseCategory(
        "service_quota_exceeded",
        GROUP_INFRASTRUCTURE,
        "AWS/cloud account-level quota or limit reached.",
    ),
    # ── Generic fallbacks (kept for backward compatibility) ────────────
    # These exist so legacy answer keys, eval pipelines, and prior LLM
    # outputs continue to validate. New diagnoses should always prefer a
    # narrow category above; the prompt already instructs the LLM to do so.
    RootCauseCategory(
        "configuration_error",
        GROUP_GENERIC,
        "Generic configuration root cause; prefer a narrower category.",
    ),
    RootCauseCategory(
        "code_defect",
        GROUP_GENERIC,
        "Generic code defect; prefer a narrower code_defect_* category.",
    ),
    RootCauseCategory(
        "data_quality",
        GROUP_GENERIC,
        "Generic data quality root cause; prefer a narrower data_* category.",
    ),
    RootCauseCategory(
        "resource_exhaustion",
        GROUP_GENERIC,
        "Generic resource exhaustion fallback; prefer a narrower exhaustion category.",
    ),
    RootCauseCategory(
        "dependency_failure",
        GROUP_GENERIC,
        "Generic dependency failure; prefer a narrower upstream_* category.",
    ),
    RootCauseCategory(
        "infrastructure",
        GROUP_GENERIC,
        "Generic infrastructure fallback; prefer a narrower infrastructure category.",
    ),
    RootCauseCategory(
        "cpu_saturation",
        GROUP_GENERIC,
        "Generic CPU saturation fallback; prefer cpu_saturation_bad_query / workload_burst.",
    ),
    RootCauseCategory(
        "replication_lag",
        GROUP_GENERIC,
        "Generic replication lag fallback; prefer replication_lag_wal_volume / long_query_on_replica.",
    ),
    RootCauseCategory(
        "healthy",
        GROUP_GENERIC,
        "All metrics within normal bounds; alert is informational or stale.",
    ),
    RootCauseCategory(
        "unknown",
        GROUP_GENERIC,
        "Insufficient evidence to commit to a category.",
    ),
)


VALID_ROOT_CAUSE_CATEGORIES: frozenset[str] = frozenset(entry.name for entry in _TAXONOMY)


# Names that should never be the diagnosis target for a real incident, but
# remain valid string outputs (e.g. ``healthy`` short-circuit, ``unknown``
# insufficient-evidence path). Useful for prompt builders that want to
# exclude these from the "preferred narrow categories" list.
GENERIC_FALLBACK_CATEGORIES: frozenset[str] = frozenset(
    entry.name for entry in _TAXONOMY if entry.group == GROUP_GENERIC
)


def categories_by_group() -> dict[str, list[RootCauseCategory]]:
    """Return categories grouped in the canonical display order.

    Preserves source order within each group so the prompt list stays
    stable across deployments — important for prompt cache hits and
    deterministic agent traces.
    """
    grouped: dict[str, list[RootCauseCategory]] = {group: [] for group in _GROUP_ORDER}
    for entry in _TAXONOMY:
        grouped[entry.group].append(entry)
    return grouped


def render_prompt_taxonomy() -> str:
    """Render the taxonomy as a multi-line string for inclusion in prompts.

    The output is a grouped, line-per-category list: each category is shown
    as ``name — description``, with section headers per group. This is the
    only format the diagnosis prompt should embed; callers must not
    hard-code category strings inline so that adding a new category in this
    module is automatically reflected in the prompt without surgery
    elsewhere.
    """
    lines: list[str] = []
    for group, entries in categories_by_group().items():
        if not entries:
            continue
        lines.append(f"[{group}]")
        for entry in entries:
            lines.append(f"- {entry.name} — {entry.description}")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


__all__ = [
    "GENERIC_FALLBACK_CATEGORIES",
    "GROUP_CLOUD_STORAGE",
    "GROUP_CODE_AND_CONFIG",
    "GROUP_DATABASE",
    "GROUP_DATA_PIPELINE",
    "GROUP_DEPENDENCY",
    "GROUP_GENERIC",
    "GROUP_INFRASTRUCTURE",
    "GROUP_KUBERNETES",
    "GROUP_NETWORK",
    "GROUP_WORKLOAD",
    "RootCauseCategory",
    "VALID_ROOT_CAUSE_CATEGORIES",
    "categories_by_group",
    "render_prompt_taxonomy",
]
