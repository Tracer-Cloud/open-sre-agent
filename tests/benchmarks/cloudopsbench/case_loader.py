from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

SUITE_DIR = Path(__file__).resolve().parent
CLOUDOPSBENCH_HF_DATASET_ID = "tracer-cloud/cloud-ops-bench-dataset"
BENCHMARK_DIR = Path(os.environ.get("CLOUDOPSBENCH_BENCHMARK_DIR", SUITE_DIR / "benchmark"))

EXPECTED_CASE_COUNTS: dict[tuple[str, str], int] = {
    ("boutique", "admission"): 58,
    ("boutique", "infrastructure"): 48,
    ("boutique", "performance"): 21,
    ("boutique", "runtime"): 45,
    ("boutique", "scheduling"): 164,
    ("boutique", "service"): 54,
    ("boutique", "startup"): 62,
    ("trainticket", "performance"): 47,
    ("trainticket", "runtime"): 96,
    ("trainticket", "service"): 37,
    ("trainticket", "startup"): 24,
}

VALID_TAXONOMIES = {
    "Admission_Fault",
    "Scheduling_Fault",
    "Infrastructure_Fault",
    "Startup_Fault",
    "Runtime_Fault",
    "Service_Routing_Fault",
    "Performance_Fault",
}

VALID_ROOT_CAUSES = {
    "namespace_cpu_quota_exceeded",
    "namespace_memory_quota_exceeded",
    "namespace_pod_quota_exceeded",
    "namespace_service_quota_exceeded",
    "namespace_storage_quota_exceeded",
    "missing_service_account",
    "node_cordon_mismatch",
    "node_affinity_mismatch",
    "node_selector_mismatch",
    "pod_anti_affinity_conflict",
    "taint_toleration_mismatch",
    "cpu_capacity_mismatch",
    "memory_capacity_mismatch",
    "node_network_delay",
    "node_network_packet_loss",
    "containerd_unavailable",
    "kubelet_unavailable",
    "kube_proxy_unavailable",
    "kube_scheduler_unavailable",
    "image_registry_dns_failure",
    "incorrect_image_reference",
    "missing_image_pull_secret",
    "pvc_selector_mismatch",
    "pvc_storage_class_mismatch",
    "pvc_access_mode_mismatch",
    "pvc_capacity_mismatch",
    "pv_binding_occupied",
    "volume_mount_permission_denied",
    "oom_killed",
    "liveness_probe_incorrect_protocol",
    "liveness_probe_incorrect_port",
    "liveness_probe_incorrect_timing",
    "readiness_probe_incorrect_protocol",
    "readiness_probe_incorrect_port",
    "service_selector_mismatch",
    "service_port_mapping_mismatch",
    "service_protocol_mismatch",
    "service_env_var_address_mismatch",
    "pod_cpu_overload",
    "pod_network_delay",
    "service_sidecar_port_conflict",
    "service_dns_resolution_failure",
    "mysql_invalid_credentials",
    "mysql_invalid_port",
    "missing_secret_binding",
    "db_connection_exhaustion",
    "db_readonly_mode",
    "gateway_misrouted",
    "deployment_zero_replicas",
}


@dataclass(frozen=True)
class CloudOpsGroundTruth:
    fault_taxonomy: str
    fault_object: str
    root_cause: str


@dataclass(frozen=True)
class CloudOpsCase:
    case_id: str
    system: str
    fault_category: str
    case_name: str
    case_dir: Path
    metadata_path: Path
    tool_cache_path: Path
    namespace: str
    query: str
    result: CloudOpsGroundTruth
    process: dict[str, list[str]]
    metadata: dict[str, Any]


@dataclass(frozen=True)
class CloudOpsValidationReport:
    total_cases: int
    slice_counts: dict[str, int]
    file_count: int
    errors: list[str]

    @property
    def ok(self) -> bool:
        return not self.errors


def read_json_object(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"Expected JSON object in {path}")
    return payload


def normalize_system_name(system: str) -> str:
    cleaned = system.strip().lower()
    if cleaned in {"trainticket", "train-ticket"}:
        return "trainticket"
    if cleaned == "boutique":
        return "boutique"
    raise ValueError(f"Unsupported CloudOps system: {system}")


def case_root(
    system: str,
    fault_category: str,
    case_name: str,
    benchmark_dir: Path = BENCHMARK_DIR,
) -> Path:
    return benchmark_dir / normalize_system_name(system) / fault_category / case_name


def _parse_ground_truth(raw: dict[str, Any], metadata_path: Path) -> CloudOpsGroundTruth:
    result = raw.get("result")
    if not isinstance(result, dict):
        raise ValueError(f"{metadata_path}: missing object result")

    fault_taxonomy = str(result.get("fault_taxonomy") or "").strip()
    fault_object = str(result.get("fault_object") or "").strip()
    root_cause = str(result.get("root_cause") or "").strip()
    if fault_taxonomy not in VALID_TAXONOMIES:
        raise ValueError(f"{metadata_path}: invalid fault_taxonomy {fault_taxonomy!r}")
    if root_cause not in VALID_ROOT_CAUSES:
        raise ValueError(f"{metadata_path}: invalid root_cause {root_cause!r}")
    if not fault_object or "/" not in fault_object:
        raise ValueError(f"{metadata_path}: invalid fault_object {fault_object!r}")

    return CloudOpsGroundTruth(
        fault_taxonomy=fault_taxonomy,
        fault_object=fault_object,
        root_cause=root_cause,
    )


def _parse_process(raw: dict[str, Any], metadata_path: Path) -> dict[str, list[str]]:
    process = raw.get("process")
    if not isinstance(process, dict):
        raise ValueError(f"{metadata_path}: missing object process")

    parsed: dict[str, list[str]] = {}
    for path_name in ("path1", "path2"):
        steps = process.get(path_name)
        if not isinstance(steps, list) or not all(isinstance(step, str) for step in steps):
            raise ValueError(f"{metadata_path}: process.{path_name} must be a list of strings")
        parsed[path_name] = list(steps)
    return parsed


def load_case(
    system: str,
    fault_category: str,
    case_name: str,
    benchmark_dir: Path = BENCHMARK_DIR,
) -> CloudOpsCase:
    normalized_system = normalize_system_name(system)
    case_dir = case_root(normalized_system, fault_category, case_name, benchmark_dir)
    metadata_path = case_dir / "metadata.json"
    tool_cache_path = case_dir / "tool_cache.json"
    if not metadata_path.is_file():
        raise FileNotFoundError(f"metadata.json not found: {metadata_path}")
    if not tool_cache_path.is_file():
        raise FileNotFoundError(f"tool_cache.json not found: {tool_cache_path}")

    metadata = read_json_object(metadata_path)
    namespace = str(metadata.get("namespace") or "").strip()
    query = str(metadata.get("query") or "").strip()
    if namespace not in {"boutique", "train-ticket"}:
        raise ValueError(f"{metadata_path}: invalid namespace {namespace!r}")
    if not query:
        raise ValueError(f"{metadata_path}: query is required")

    return CloudOpsCase(
        case_id=f"{normalized_system}/{fault_category}/{case_name}",
        system=normalized_system,
        fault_category=fault_category,
        case_name=case_name,
        case_dir=case_dir,
        metadata_path=metadata_path,
        tool_cache_path=tool_cache_path,
        namespace=namespace,
        query=query,
        result=_parse_ground_truth(metadata, metadata_path),
        process=_parse_process(metadata, metadata_path),
        metadata=metadata,
    )


def iter_case_ids(
    benchmark_dir: Path = BENCHMARK_DIR,
    *,
    system: str | None = None,
    fault_category: str | None = None,
) -> list[tuple[str, str, str]]:
    if not benchmark_dir.is_dir():
        raise FileNotFoundError(f"CloudOps benchmark directory not found: {benchmark_dir}")

    selected_system = normalize_system_name(system) if system else None
    case_ids: list[tuple[str, str, str]] = []
    for system_dir in sorted(path for path in benchmark_dir.iterdir() if path.is_dir()):
        if selected_system and system_dir.name != selected_system:
            continue
        for fault_dir in sorted(path for path in system_dir.iterdir() if path.is_dir()):
            if fault_category and fault_dir.name != fault_category:
                continue
            for case_dir in sorted(path for path in fault_dir.iterdir() if path.is_dir()):
                case_ids.append((system_dir.name, fault_dir.name, case_dir.name))
    return case_ids


def load_cases(
    benchmark_dir: Path = BENCHMARK_DIR,
    *,
    system: str | None = None,
    fault_category: str | None = None,
    case_name: str | None = None,
    limit: int | None = None,
) -> list[CloudOpsCase]:
    ids = iter_case_ids(benchmark_dir, system=system, fault_category=fault_category)
    if case_name:
        ids = [case_id for case_id in ids if case_id[2] == case_name]
    if limit is not None:
        ids = ids[:limit]
    return [load_case(*case_id, benchmark_dir=benchmark_dir) for case_id in ids]


def build_alert(case: CloudOpsCase) -> dict[str, Any]:
    cluster_name = f"cloudopsbench-{case.system}"
    return {
        "alert_source": "cloudopsbench",
        "title": f"CloudOpsBench {case.case_id}",
        "status": "firing",
        "startsAt": "2024-01-01T00:00:00Z",
        "commonLabels": {
            "alertname": "CloudOpsBenchRCA",
            "severity": "critical",
            "pipeline_name": "cloudopsbench",
            "system": case.system,
            "fault_category": case.fault_category,
            "case_name": case.case_name,
        },
        "commonAnnotations": {
            "summary": case.query,
            "description": (
                f"The Kubernetes environment in namespace `{case.namespace}` is experiencing "
                f"a fault. Diagnose the root cause of this incident."
            ),
            "namespace": case.namespace,
            "kube_namespace": case.namespace,
            "cluster_name": cluster_name,
            "eks_cluster": cluster_name,
            "cloudopsbench_case_id": case.case_id,
            "cloudopsbench_system": case.system,
            "cloudopsbench_fault_category": case.fault_category,
            "cloudopsbench_case_name": case.case_name,
        },
    }


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def validate_corpus(benchmark_dir: Path = BENCHMARK_DIR) -> CloudOpsValidationReport:
    errors: list[str] = []
    slice_counts: dict[str, int] = {}
    file_count = 0

    if not benchmark_dir.is_dir():
        return CloudOpsValidationReport(
            total_cases=0,
            slice_counts={},
            file_count=0,
            errors=[
                f"benchmark directory not found: {benchmark_dir}. "
                "Run `make download-cloudopsbench-hf` first."
            ],
        )

    for system_dir in sorted(path for path in benchmark_dir.iterdir() if path.is_dir()):
        for fault_dir in sorted(path for path in system_dir.iterdir() if path.is_dir()):
            case_dirs = sorted(path for path in fault_dir.iterdir() if path.is_dir())
            slice_counts[f"{system_dir.name}/{fault_dir.name}"] = len(case_dirs)
            for case_dir in case_dirs:
                try:
                    load_case(system_dir.name, fault_dir.name, case_dir.name, benchmark_dir)
                except (OSError, ValueError, json.JSONDecodeError) as exc:
                    errors.append(str(exc))

    expected_keys = {f"{system}/{fault}" for system, fault in EXPECTED_CASE_COUNTS}
    actual_keys = set(slice_counts)
    for missing in sorted(expected_keys - actual_keys):
        errors.append(f"missing slice: {missing}")
    for unexpected in sorted(actual_keys - expected_keys):
        errors.append(f"unexpected slice: {unexpected}")
    for (system, fault), expected in EXPECTED_CASE_COUNTS.items():
        actual = slice_counts.get(f"{system}/{fault}")
        if actual != expected:
            errors.append(f"count mismatch {system}/{fault}: expected {expected}, got {actual}")

    file_count = sum(1 for path in benchmark_dir.rglob("*") if path.is_file())
    return CloudOpsValidationReport(
        total_cases=sum(slice_counts.values()),
        slice_counts=slice_counts,
        file_count=file_count,
        errors=errors,
    )
