"""Closed-vocabulary constants for CloudOpsBench scoring and prediction.

Package-level module (not under ``predictor/``) so ``taxonomy`` and
``scoring`` can import fault-object lists without loading
``predictor/__init__.py``. The predictor subpackage re-exports these
from ``predictor.vocabulary`` for backward compatibility.
"""

from __future__ import annotations

# Declaring ``__all__`` tells CodeQL that these names are the module's
# public surface — they are not "unused globals" but intentional
# re-exports consumed by ``taxonomy``, ``scoring``, and
# ``predictor.vocabulary`` (which re-exports them for legacy callers).
__all__ = [
    "_FAULT_OBJECT_NAMESPACES",
    "_FAULT_OBJECT_NODES",
    "_FAULT_OBJECT_SERVICES",
    "_ROOT_CAUSES",
    "_TAXONOMY_CATEGORIES",
]

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
    # Performance
    "pod_network_delay",
    "pod_cpu_overload",
    # Admission
    "namespace_cpu_quota_exceeded",
    "namespace_memory_quota_exceeded",
    "namespace_pod_quota_exceeded",
    "namespace_service_quota_exceeded",
    "namespace_storage_quota_exceeded",
)

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
    # train-ticket
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
