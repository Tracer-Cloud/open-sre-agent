"""Shared root-cause → taxonomy mapping and fault-object inference.

Single module for scoring (L0 investigation parsing), the predictor stage
(L1 formalization), and investigation handoff (B1). Lives outside
``predictor/`` so ``scoring`` can import vocabulary-backed helpers without
loading ``predictor/__init__.py`` or creating a circular import with
``investigation_handoff``.

Keep in lock-step with ``predictor.vocabulary`` — the scorer compares exact
strings after ``normalize_text``.
"""

from __future__ import annotations

from tests.benchmarks.cloudopsbench.closed_vocabulary import (
    _FAULT_OBJECT_NAMESPACES,
    _FAULT_OBJECT_NODES,
    _FAULT_OBJECT_SERVICES,
)

# Declaring ``__all__`` tells CodeQL that the underscore-prefixed
# backward-compat aliases at the bottom of this file are not "unused
# globals" but intentional re-exports for callers that hardcoded the
# pre-refactor ``_infer_fault_object`` / ``_taxonomy_for_root_cause``
# names (e.g. ``scoring`` re-aliases through these).
__all__ = [
    "_FAULT_OBJECT_SERVICES_BY_LENGTH",
    "_infer_fault_object",
    "_taxonomy_for_root_cause",
    "infer_fault_object",
    "taxonomy_for_root_cause",
]

# Longest service names first so ``ts-order-other-service`` shadows
# ``ts-order-service`` on substring match.
_FAULT_OBJECT_SERVICES_BY_LENGTH: tuple[str, ...] = tuple(
    sorted(_FAULT_OBJECT_SERVICES, key=len, reverse=True)
)


def infer_fault_object(text: str) -> str:
    """Substring match against the closed service vocabulary.

    Longest names first (precomputed in ``_FAULT_OBJECT_SERVICES_BY_LENGTH``)
    so ``ts-order-other-service`` wins over ``ts-order-service``. Kept in
    sync with ``predictor.vocabulary._FAULT_OBJECT_*``.

    Namespace match REQUIRES the literal word ``namespace`` to appear in
    the text as a precision guard. Without it, prose like "boutique system
    has memory pressure" would incorrectly return ``namespace/boutique``
    whenever the cluster name is mentioned in passing.
    """
    for service_name in _FAULT_OBJECT_SERVICES_BY_LENGTH:
        if service_name in text:
            return f"app/{service_name}"
    for node_name in _FAULT_OBJECT_NODES:
        if node_name in text:
            return f"node/{node_name}"
    if "namespace" in text:
        for ns_name in _FAULT_OBJECT_NAMESPACES:
            if ns_name in text:
                return f"namespace/{ns_name}"
    return ""


def taxonomy_for_root_cause(root_cause: str) -> str:
    """Map a CloudOpsBench root_cause to its paper-taxonomy bucket.

    Audited against the dataset's actual ground-truth ``fault_taxonomy``
    values (metadata.json under ``benchmark/``), not derived independently.
    """
    if root_cause.startswith("namespace_") or root_cause == "missing_service_account":
        return "Admission_Fault"
    if root_cause in {
        "node_cordon_mismatch",
        "node_affinity_mismatch",
        "node_selector_mismatch",
        "pod_anti_affinity_conflict",
        "taint_toleration_mismatch",
        "cpu_capacity_mismatch",
        "memory_capacity_mismatch",
    }:
        return "Scheduling_Fault"
    if root_cause in {
        "node_network_delay",
        "node_network_packet_loss",
        "containerd_unavailable",
        "kubelet_unavailable",
        "kube_proxy_unavailable",
        "kube_scheduler_unavailable",
    }:
        return "Infrastructure_Fault"
    if root_cause in {
        "image_registry_dns_failure",
        "incorrect_image_reference",
        "missing_image_pull_secret",
        "missing_secret_binding",
        "pvc_selector_mismatch",
        "pvc_storage_class_mismatch",
        "pvc_access_mode_mismatch",
        "pvc_capacity_mismatch",
        "pv_binding_occupied",
        "volume_mount_permission_denied",
    }:
        return "Startup_Fault"
    if root_cause in {
        "oom_killed",
        "liveness_probe_incorrect_protocol",
        "liveness_probe_incorrect_port",
        "liveness_probe_incorrect_timing",
        "readiness_probe_incorrect_protocol",
        "readiness_probe_incorrect_port",
        "mysql_invalid_credentials",
        "mysql_invalid_port",
        "db_connection_exhaustion",
        "db_readonly_mode",
        "gateway_misrouted",
        "deployment_zero_replicas",
        "service_sidecar_port_conflict",
    }:
        return "Runtime_Fault"
    if root_cause in {
        "service_selector_mismatch",
        "service_port_mapping_mismatch",
        "service_protocol_mismatch",
        "service_env_var_address_mismatch",
        "service_dns_resolution_failure",
    }:
        return "Service_Routing_Fault"
    return "Performance_Fault"


# Backward-compatible aliases used across the bench codebase.
_infer_fault_object = infer_fault_object
_taxonomy_for_root_cause = taxonomy_for_root_cause
