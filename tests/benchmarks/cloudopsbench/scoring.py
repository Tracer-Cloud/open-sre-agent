from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass
from typing import Any

from tests.benchmarks.cloudopsbench.case_loader import CloudOpsCase
from tests.benchmarks.cloudopsbench.replay_backend import normalize_resource_type

TOOL_KEY_PARAMS: dict[str, list[str]] = {
    "GetResources": ["resource_type"],
    "DescribeResource": ["resource_type", "name"],
    "CheckNodeServiceStatus": ["node_name", "service_name"],
    "GetClusterConfiguration": [],
    "GetAlerts": [],
    "GetErrorLogs": ["service_name"],
    "GetRecentLogs": ["service_name"],
    "GetServiceDependencies": ["service_name"],
    "GetAppYAML": ["app_name"],
    "CheckServiceConnectivity": ["service_name", "port"],
}

TOOL_REQUIRED_PARAMS: dict[str, list[str]] = {
    "GetResources": ["resource_type"],
    "DescribeResource": ["resource_type", "name"],
    "CheckNodeServiceStatus": ["node_name", "service_name"],
    "GetClusterConfiguration": [],
    "GetAlerts": [],
    "GetErrorLogs": ["service_name"],
    "GetRecentLogs": ["service_name"],
    "GetServiceDependencies": ["service_name"],
    "GetAppYAML": ["app_name"],
    "CheckServiceConnectivity": ["service_name", "port"],
}


@dataclass(frozen=True)
class CloudOpsMetrics:
    a1: float
    a3: float
    partial_a1: float
    partial_a3: float
    tcr: float
    exact: float
    in_order: float
    any_order: float
    rel: float
    cov: float
    steps: float
    mtti: float
    iac: float
    rar: float
    ztdr: float


@dataclass(frozen=True)
class CloudOpsCaseScore:
    case_id: str
    ground_truth: dict[str, Any]
    top_3_predictions: list[dict[str, Any]]
    final_answer_source: str
    standardized_agent_steps: list[str]
    expert_steps: list[str]
    matched_path: str
    invalid_reasons: list[str]
    metrics: CloudOpsMetrics
    error: str = ""

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["metrics"] = asdict(self.metrics)
        return payload


def normalize_text(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip().lower()


def strip_pod_suffix(name: Any) -> str:
    if not isinstance(name, str):
        return str(name)
    patterns = [
        r"^([a-z0-9-]+)-[a-f0-9]{8,10}-[a-z0-9]{4,6}$",
        r"^([a-z0-9-]+)-[a-z0-9]{5}$",
    ]
    for pattern in patterns:
        match = re.match(pattern, name)
        if match:
            return match.group(1)
    return name


def parse_json_maybe(raw: Any) -> dict[str, Any] | None:
    if isinstance(raw, dict):
        return raw
    if raw is None:
        return None
    text = str(raw).strip()
    if not text:
        return None
    for pattern in (r"```json\s*(.*?)\s*```", r"```\s*(.*?)\s*```"):
        match = re.search(pattern, text, re.DOTALL)
        if match:
            text = match.group(1).strip()
            break
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        return None
    return parsed if isinstance(parsed, dict) else None


def extract_final_answer_payload(case_data: dict[str, Any]) -> tuple[dict[str, Any] | None, str]:
    candidates: list[tuple[str, Any]] = [
        ("top_level_final_answer", case_data.get("final_answer")),
        ("root_cause", case_data.get("root_cause")),
        ("report", case_data.get("report")),
    ]
    final_state = case_data.get("final_state")
    if isinstance(final_state, dict):
        candidates.extend(
            [
                ("final_state_final_answer", final_state.get("final_answer")),
                ("final_state_root_cause", final_state.get("root_cause")),
                ("final_state_report", final_state.get("report")),
            ]
        )
    for step in reversed(case_data.get("steps", [])):
        if not isinstance(step, dict):
            continue
        if step.get("final_answer"):
            candidates.append((f"step_{step.get('step_id', 'unknown')}_final_answer", step))
        if step.get("raw_model_output"):
            candidates.append((f"step_{step.get('step_id', 'unknown')}_raw_model_output", step))

    for source, candidate in candidates:
        parsed = parse_json_maybe(candidate)
        if parsed and isinstance(parsed.get("top_3_predictions"), list):
            return parsed, source

    inferred = infer_final_answer_from_opensre_text(case_data)
    if inferred is not None:
        return inferred, "inferred_from_opensre_text"
    return None, "unparsed"


def infer_final_answer_from_opensre_text(case_data: dict[str, Any]) -> dict[str, Any] | None:
    final_state = case_data.get("final_state")
    texts = [
        case_data.get("root_cause"),
        case_data.get("report"),
        case_data.get("final_answer"),
    ]
    if isinstance(final_state, dict):
        texts.extend(
            [
                final_state.get("root_cause"),
                final_state.get("report"),
                " ".join(str(item) for item in final_state.get("causal_chain", [])),
                " ".join(
                    str(claim.get("claim", ""))
                    for claim in final_state.get("validated_claims", [])
                    if isinstance(claim, dict)
                ),
            ]
        )
    text = " ".join(str(item or "") for item in texts).lower()
    if not text.strip():
        return None

    root_cause = _infer_root_cause(text)
    fault_object = _infer_fault_object(text)
    if not root_cause or not fault_object:
        return None

    return {
        "key_evidence_summary": "Inferred from OpenSRE RCA text for CloudOpsBench scoring.",
        "top_3_predictions": [
            {
                "rank": 1,
                "fault_taxonomy": _taxonomy_for_root_cause(root_cause),
                "fault_object": fault_object,
                "root_cause": root_cause,
            }
        ],
    }


def _infer_root_cause(text: str) -> str:
    checks = [
        (
            "service_env_var_address_mismatch",
            ("env", "environment", "address", "hostname", "redis-cart-invalid", "invalid"),
        ),
        ("service_dns_resolution_failure", ("dns", "resolution", "no such host")),
        ("service_selector_mismatch", ("selector", "endpoint", "no endpoints")),
        ("service_port_mapping_mismatch", ("port mapping", "targetport", "target port")),
        ("pod_cpu_overload", ("cpu", "overload", "saturation")),
        ("oom_killed", ("oom", "out of memory", "oomkilled")),
        ("incorrect_image_reference", ("imagepullbackoff", "image pull", "incorrect image")),
        ("missing_image_pull_secret", ("image pull secret", "pull secret")),
        ("deployment_zero_replicas", ("zero replicas", "replica count is 0")),
        ("db_connection_exhaustion", ("connection exhaustion", "too many connections")),
        ("mysql_invalid_credentials", ("mysql", "access denied", "invalid credentials")),
        ("mysql_invalid_port", ("mysql", "invalid port", "wrong port")),
        ("node_network_delay", ("node", "network delay")),
        ("node_network_packet_loss", ("packet loss",)),
        ("kubelet_unavailable", ("kubelet", "unavailable")),
        ("containerd_unavailable", ("containerd", "unavailable")),
        ("kube_proxy_unavailable", ("kube-proxy", "unavailable")),
        ("kube_scheduler_unavailable", ("kube-scheduler", "unavailable")),
    ]
    for root_cause, tokens in checks:
        if all(token in text for token in tokens):
            return root_cause
    return ""


def _infer_fault_object(text: str) -> str:
    service_names = [
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
        "ts-gateway-service",
        "ts-order-service",
        "ts-payment-service",
        "ts-travel-service",
        "ts-user-service",
        "ts-auth-service",
        "ts-route-service",
        "ts-ticket-office-service",
    ]
    for service_name in service_names:
        if service_name in text:
            return f"app/{service_name}"
    for node_name in ("master", "worker-01", "worker-02", "worker-03"):
        if node_name in text:
            return f"node/{node_name}"
    if "namespace" in text and "boutique" in text:
        return "namespace/boutique"
    if "namespace" in text and "train-ticket" in text:
        return "namespace/train-ticket"
    return ""


def _taxonomy_for_root_cause(root_cause: str) -> str:
    if root_cause.startswith("namespace_"):
        return "Admission_Fault"
    if root_cause in {
        "missing_service_account",
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
        "missing_secret_binding",
        "db_connection_exhaustion",
        "db_readonly_mode",
        "gateway_misrouted",
        "deployment_zero_replicas",
    }:
        return "Runtime_Fault"
    if root_cause in {
        "service_selector_mismatch",
        "service_port_mapping_mismatch",
        "service_protocol_mismatch",
        "service_env_var_address_mismatch",
        "service_sidecar_port_conflict",
        "service_dns_resolution_failure",
    }:
        return "Service_Routing_Fault"
    return "Performance_Fault"


def compare_prediction(
    prediction: dict[str, Any], ground_truth: dict[str, Any]
) -> tuple[bool, bool]:
    gt_tax = normalize_text(ground_truth.get("fault_taxonomy"))
    gt_obj = normalize_text(ground_truth.get("fault_object"))
    gt_root = normalize_text(ground_truth.get("root_cause"))
    pr_tax = normalize_text(prediction.get("fault_taxonomy"))
    pr_obj = normalize_text(prediction.get("fault_object"))
    pr_root = normalize_text(prediction.get("root_cause"))
    full_match = pr_tax == gt_tax and pr_obj == gt_obj and pr_root == gt_root
    partial_match = pr_obj == gt_obj and pr_root == gt_root
    return full_match, partial_match


def score_predictions(
    predictions: list[dict[str, Any]],
    ground_truth: dict[str, Any],
) -> dict[str, float]:
    a1 = 0.0
    a3 = 0.0
    partial_a1 = 0.0
    partial_a3 = 0.0
    for idx, prediction in enumerate(predictions[:3]):
        full_match, partial_match = compare_prediction(prediction, ground_truth)
        if full_match:
            if idx == 0:
                a1 = 1.0
            a3 = 1.0
        if partial_match:
            if idx == 0:
                partial_a1 = 1.0
            partial_a3 = 1.0
    return {
        "a1": a1,
        "a3": a3,
        "partial_a1": partial_a1,
        "partial_a3": partial_a3,
    }


def standardize_tool_step(step: dict[str, Any]) -> tuple[str | None, str | None]:
    action_name = step.get("action_name")
    action_input = step.get("action_input")
    if not action_name or not isinstance(action_name, str):
        return None, "missing_action_name"
    if not isinstance(action_input, dict):
        return None, "invalid_action_input"

    required = TOOL_REQUIRED_PARAMS.get(action_name, [])
    for key in required:
        value = action_input.get(key)
        if value is None or str(value).strip() == "":
            return None, f"missing_required_param:{key}"

    if action_name == "GetResources":
        resource_type = normalize_resource_type(action_input.get("resource_type"))
        if not resource_type:
            return None, "missing_required_param:resource_type"
        return f"{action_name}::{resource_type}", None

    if action_name == "DescribeResource":
        resource_type = normalize_resource_type(action_input.get("resource_type"))
        name = action_input.get("name")
        if resource_type == "pods":
            name = strip_pod_suffix(name)
        elif name is not None:
            name = str(name).strip()
        if not resource_type or not name:
            return None, "missing_describe_resource_fields"
        return f"{action_name}::{resource_type}::{name}", None

    params = TOOL_KEY_PARAMS.get(action_name)
    if params is None:
        params = sorted(key for key in action_input if key != "namespace")

    parts = [action_name]
    for key in params:
        if key == "namespace":
            continue
        value = action_input.get(key)
        if value is None or str(value).strip() == "":
            continue
        parts.append(str(value).strip())
    if len(parts) == 1:
        parts.append("")
    return "::".join(parts), None


def standardize_agent_steps(case_data: dict[str, Any]) -> tuple[list[str], int, list[str]]:
    provided_steps = case_data.get("standardized_agent_steps")
    if isinstance(provided_steps, list) and all(isinstance(step, str) for step in provided_steps):
        return list(provided_steps), 0, []

    standardized: list[str] = []
    invalid_reasons: list[str] = []
    invalid_count = 0
    for step in case_data.get("steps", []):
        if not isinstance(step, dict) or step.get("action_type") != "tool":
            continue
        if step.get("error"):
            invalid_count += 1
            invalid_reasons.append(f"step_{step.get('step_id', 'unknown')}:error")
            continue
        standardized_step, reason = standardize_tool_step(step)
        if reason:
            invalid_count += 1
            invalid_reasons.append(f"step_{step.get('step_id', 'unknown')}:{reason}")
            continue
        if standardized_step is not None:
            standardized.append(standardized_step)
    return standardized, invalid_count, invalid_reasons


def precision_recall_f1(
    agent_steps: list[str], expert_steps: list[str]
) -> tuple[float, float, float]:
    if not agent_steps and not expert_steps:
        return 1.0, 1.0, 1.0
    if not agent_steps:
        return 0.0, 0.0, 0.0
    agent_set = set(agent_steps)
    expert_set = set(expert_steps)
    intersection = len(agent_set & expert_set)
    precision = intersection / len(agent_set) if agent_set else 0.0
    recall = intersection / len(expert_set) if expert_set else 0.0
    f1 = 2 * precision * recall / (precision + recall) if precision + recall > 0 else 0.0
    return precision, recall, f1


def in_order_match(expert_steps: list[str], agent_steps: list[str]) -> float:
    if not expert_steps:
        return 1.0
    idx = 0
    for step in agent_steps:
        if step == expert_steps[idx]:
            idx += 1
            if idx == len(expert_steps):
                return 1.0
    return 0.0


def any_order_match(expert_steps: list[str], agent_steps: list[str]) -> float:
    if not expert_steps:
        return 1.0
    return 1.0 if set(expert_steps).issubset(set(agent_steps)) else 0.0


def exact_match(expert_steps: list[str], agent_steps: list[str]) -> float:
    return 1.0 if expert_steps == agent_steps else 0.0


def choose_best_path(agent_steps: list[str], process: dict[str, list[str]]) -> dict[str, Any]:
    best: dict[str, Any] | None = None
    for path_name in ("path1", "path2"):
        expert_steps = process.get(path_name, [])
        precision, recall, f1 = precision_recall_f1(agent_steps, expert_steps)
        current = {
            "matched_path": path_name,
            "expert_steps": expert_steps,
            "rel": precision,
            "cov": recall,
            "f1": f1,
            "in_order": in_order_match(expert_steps, agent_steps),
            "exact": exact_match(expert_steps, agent_steps),
            "any_order": any_order_match(expert_steps, agent_steps),
        }
        if best is None or current["f1"] > best["f1"]:
            best = current
            continue
        if current["f1"] == best["f1"] and current["in_order"] > best["in_order"]:
            best = current
    return best or {
        "matched_path": "path1",
        "expert_steps": [],
        "rel": 0.0,
        "cov": 0.0,
        "f1": 0.0,
        "in_order": 0.0,
        "exact": 0.0,
        "any_order": 0.0,
    }


def calculate_rar(agent_steps: list[str]) -> float:
    total = len(agent_steps)
    if total == 0:
        return 0.0
    counts: dict[str, int] = {}
    for step in agent_steps:
        counts[step] = counts.get(step, 0) + 1
    redundant = sum(count - 1 for count in counts.values())
    return redundant / total


def calculate_total_latency(case_data: dict[str, Any]) -> float:
    total = 0.0
    for step in case_data.get("steps", []):
        if not isinstance(step, dict):
            continue
        for key in ("model_latency", "tool_latency"):
            value = step.get(key)
            if isinstance(value, (int, float)):
                total += float(value)
    return total


def score_case(case: CloudOpsCase, case_data: dict[str, Any]) -> CloudOpsCaseScore:
    ground_truth = {
        "fault_taxonomy": case.result.fault_taxonomy,
        "fault_object": case.result.fault_object,
        "root_cause": case.result.root_cause,
    }
    parsed_final_answer, final_answer_source = extract_final_answer_payload(case_data)
    predictions = parsed_final_answer.get("top_3_predictions", []) if parsed_final_answer else []
    if not isinstance(predictions, list):
        predictions = []
    predictions = [prediction for prediction in predictions if isinstance(prediction, dict)]
    outcome_scores = (
        score_predictions(predictions, ground_truth)
        if predictions
        else {"a1": 0.0, "a3": 0.0, "partial_a1": 0.0, "partial_a3": 0.0}
    )

    agent_steps, invalid_count, invalid_reasons = standardize_agent_steps(case_data)
    best_path = choose_best_path(agent_steps, case.process)
    steps = len(agent_steps)
    ztdr = 1.0 if steps == 0 and predictions else 0.0
    metrics = CloudOpsMetrics(
        a1=outcome_scores["a1"],
        a3=outcome_scores["a3"],
        partial_a1=outcome_scores["partial_a1"],
        partial_a3=outcome_scores["partial_a3"],
        tcr=1.0 if predictions else 0.0,
        exact=best_path["exact"],
        in_order=best_path["in_order"],
        any_order=best_path["any_order"],
        rel=best_path["rel"],
        cov=best_path["cov"],
        steps=float(steps),
        mtti=calculate_total_latency(case_data),
        iac=float(invalid_count),
        rar=calculate_rar(agent_steps),
        ztdr=ztdr,
    )

    return CloudOpsCaseScore(
        case_id=case.case_id,
        ground_truth=ground_truth,
        top_3_predictions=predictions,
        final_answer_source=final_answer_source,
        standardized_agent_steps=agent_steps,
        expert_steps=list(best_path["expert_steps"]),
        matched_path=str(best_path["matched_path"]),
        invalid_reasons=invalid_reasons,
        metrics=metrics,
        error="" if parsed_final_answer else "unparsed_final_answer",
    )


def summarize_scores(scores: list[CloudOpsCaseScore]) -> dict[str, Any]:
    metric_names = [
        "a1",
        "a3",
        "partial_a1",
        "partial_a3",
        "tcr",
        "exact",
        "in_order",
        "any_order",
        "rel",
        "cov",
        "steps",
        "mtti",
        "iac",
        "rar",
        "ztdr",
    ]
    total = len(scores)
    sums = dict.fromkeys(metric_names, 0.0)
    parse_failures = 0
    for score in scores:
        if score.error == "unparsed_final_answer":
            parse_failures += 1
        metrics = asdict(score.metrics)
        for name in metric_names:
            sums[name] += float(metrics[name])

    averages = {name: round(sums[name] / total, 4) if total else 0.0 for name in metric_names}
    return {
        "counts": {
            "total_cases": total,
            "final_answer_parse_failures": parse_failures,
        },
        "metrics": {
            "Accuracy @1": averages["a1"],
            "Accuracy @3": averages["a3"],
            "Partial Accuracy @1": averages["partial_a1"],
            "Partial Accuracy @3": averages["partial_a3"],
            "Task Completion Rate": averages["tcr"],
            "ExactMatch": averages["exact"],
            "InOrder": averages["in_order"],
            "AnyOrder": averages["any_order"],
            "Relevant": averages["rel"],
            "Coverage": averages["cov"],
            "Steps": round(averages["steps"], 2),
            "Mean Time to Identify": round(averages["mtti"], 4),
            "Invalid Action Count": round(averages["iac"], 2),
            "Redundant Action Rate": averages["rar"],
            "Zero-Tool Direct Resolution": averages["ztdr"],
        },
    }
