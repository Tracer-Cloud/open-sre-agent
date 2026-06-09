"""Closed-vocabulary constants for the cloudopsbench paper-format predictor.

Single source of truth for the scorer's enum surfaces (root_cause,
fault_taxonomy, fault_object scopes). Kept here so the snapping (Lever A),
reranking (Lever D), prompt construction, and any future structured-output
enum schema all read from the same place.

These constants mirror tests/benchmarks/cloudopsbench/scoring.py — keep
them in lock-step with ``scoring._taxonomy_for_root_cause`` and
``scoring._infer_fault_object``: the scorer compares exact strings after
``normalize_text`` (lower-case + strip), so the values must match its enum.
"""

from __future__ import annotations

_TAXONOMY_CATEGORIES: tuple[str, ...] = (
    "Admission_Fault",
    "Scheduling_Fault",
    "Infrastructure_Fault",
    "Startup_Fault",
    "Runtime_Fault",
    "Service_Routing_Fault",
    "Performance_Fault",
)

_ROOT_CAUSES: tuple[str, ...] = (
    # Scheduling
    "missing_service_account",
    "node_cordon_mismatch",
    "node_affinity_mismatch",
    "node_selector_mismatch",
    "pod_anti_affinity_conflict",
    "taint_toleration_mismatch",
    "cpu_capacity_mismatch",
    "memory_capacity_mismatch",
    # Infrastructure
    "node_network_delay",
    "node_network_packet_loss",
    "containerd_unavailable",
    "kubelet_unavailable",
    "kube_proxy_unavailable",
    "kube_scheduler_unavailable",
    # Startup
    "image_registry_dns_failure",
    "incorrect_image_reference",
    "missing_image_pull_secret",
    "pvc_selector_mismatch",
    "pvc_storage_class_mismatch",
    "pvc_access_mode_mismatch",
    "pvc_capacity_mismatch",
    "pv_binding_occupied",
    "volume_mount_permission_denied",
    # Runtime
    "oom_killed",
    "liveness_probe_incorrect_protocol",
    "liveness_probe_incorrect_port",
    "liveness_probe_incorrect_timing",
    "readiness_probe_incorrect_protocol",
    "readiness_probe_incorrect_port",
    "mysql_invalid_credentials",
    "mysql_invalid_port",
    "missing_secret_binding",
    "db_connection_exhaustion",
    "db_readonly_mode",
    "gateway_misrouted",
    "deployment_zero_replicas",
    # Service routing
    "service_selector_mismatch",
    "service_port_mapping_mismatch",
    "service_protocol_mismatch",
    "service_env_var_address_mismatch",
    "service_sidecar_port_conflict",
    "service_dns_resolution_failure",
    # Performance — derive Performance_Fault via the scoring default bucket.
    # These were absent from the vocab through the 2026-06-06 run, so the LLM
    # was never told they were valid and ``pod_network_delay`` would mis-snap
    # onto ``node_network_delay`` (Infrastructure_Fault). That capped a1 on the
    # entire unseen-shape stratum (performance + admission) near zero even
    # though object_a1 was ~0.40. See ANALYSIS.md for that run.
    "pod_network_delay",
    "pod_cpu_overload",
    # Admission — the ``namespace_*`` quota family. ``_snap_root_cause`` already
    # passes ``namespace_*`` tokens through verbatim and the scorer maps the
    # prefix to Admission_Fault, but listing the concrete tokens here surfaces
    # them in the system prompt so the model actually emits them.
    "namespace_cpu_quota_exceeded",
    "namespace_memory_quota_exceeded",
    "namespace_pod_quota_exceeded",
    "namespace_service_quota_exceeded",
    "namespace_storage_quota_exceeded",
)

# fault_object values are canonical paths. The scorer accepts whatever
# strings the LLM emits as long as they match the case's ground-truth
# exactly (post-normalize), but giving the LLM the universe of known
# values keeps it from inventing prefixes.
_FAULT_OBJECT_SERVICES: tuple[str, ...] = (
    # online-boutique
    "adservice",
    "cartservice",
    "checkoutservice",
    "currencyservice",
    "emailservice",
    "frontend",
    "paymentservice",
    "productcatalogservice",
    "recommendationservice",
    "redis-cart",
    "shippingservice",
    # train-ticket — full enumeration of the dataset's service mesh + DB.
    # The 2026-06-09 trimmed-prompt loss diagnostic showed 33.7% of cells had
    # a GT fault_object missing from this list, capping A@1 below the paper
    # baseline on Runtime / Performance / Startup. ``tsdb-mysql`` alone was
    # 90 cells locked at A@1=0.00 because the LLM consistently substituted
    # the nearest in-vocab service (most often ``ts-inside-payment-service``).
    "ts-gateway-service",
    "ts-order-service",
    "ts-payment-service",
    "ts-travel-service",
    "ts-user-service",
    "ts-auth-service",
    "ts-route-service",
    "ts-ticket-office-service",
    "ts-assurance-service",
    "ts-basic-service",
    "ts-cancel-service",
    "ts-config-service",
    "ts-consign-service",
    "ts-contacts-service",
    "ts-delivery-service",
    "ts-food-delivery-service",
    "ts-food-service",
    "ts-inside-payment-service",
    "ts-notification-service",
    "ts-order-other-service",
    "ts-preserve-service",
    "ts-price-service",
    "ts-seat-service",
    "ts-security-service",
    "ts-station-food-service",
    "ts-station-service",
    "ts-train-food-service",
    "ts-train-service",
    "ts-travel2-service",
    "ts-voucher-service",
    "ts-wait-order-service",
    "tsdb-mysql",
)

_FAULT_OBJECT_NODES: tuple[str, ...] = ("master", "worker-01", "worker-02", "worker-03")
_FAULT_OBJECT_NAMESPACES: tuple[str, ...] = ("boutique", "train-ticket")
