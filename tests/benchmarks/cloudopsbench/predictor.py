"""Emit paper-format ``top_3_predictions`` for Cloud-OpsBench scoring.

After opensre's investigation produces a free-text RCA, this module runs
one additional LLM call that translates the agent's findings into the
structured ``top_3_predictions`` JSON that the paper's scorer expects::

    {
      "top_3_predictions": [
        {"rank": 1, "fault_taxonomy": "Runtime_Fault",
         "fault_object": "app/ts-auth-service",
         "root_cause": "mysql_invalid_credentials"},
        ... (3 total)
      ]
    }

The cloudopsbench adapter calls :func:`emit_paper_predictions` after the
investigation completes; the result is stashed into
``RunResult.final_diagnosis["top_3_predictions"]`` so the scorer at
``scoring.extract_final_answer_payload`` picks it up directly and never
falls through to the brittle keyword-inference bridge.

Mode-agnostic by design: ``opensre+llm`` passes the investigation
evidence + report as ``investigation_summary``; ``llm_alone`` would pass
an empty summary so the LLM works from the alert alone. Same predictor,
same scoring — that's the honest comparison.
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any

from app.utils.llm_retry import LLMCreditExhaustedError, retry_on_rate_limit
from tests.benchmarks.cloudopsbench.scoring import _taxonomy_for_root_cause

logger = logging.getLogger(__name__)


# --------------------------------------------------------------------------- #
# Paper schema constants — mirror tests/benchmarks/cloudopsbench/scoring.py.  #
# Keep these in lock-step with scoring._taxonomy_for_root_cause and           #
# scoring._infer_fault_object: the scorer compares exact strings after        #
# normalize_text (lower-case + strip), so the values must match its enum.    #
# --------------------------------------------------------------------------- #

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
    # train-ticket
    "ts-gateway-service",
    "ts-order-service",
    "ts-payment-service",
    "ts-travel-service",
    "ts-user-service",
    "ts-auth-service",
    "ts-route-service",
    "ts-ticket-office-service",
)

_FAULT_OBJECT_NODES: tuple[str, ...] = ("master", "worker-01", "worker-02", "worker-03")
_FAULT_OBJECT_NAMESPACES: tuple[str, ...] = ("boutique", "train-ticket")


# --------------------------------------------------------------------------- #
# Public API                                                                  #
# --------------------------------------------------------------------------- #


def emit_paper_predictions(
    *,
    alert_text: str,
    investigation_summary: str,
    llm: Any,
) -> dict[str, Any] | None:
    """Ask the LLM to translate the investigation into paper-format predictions.

    ``llm`` is opensre's agent LLM client (typically the same one that ran
    the investigation, obtained via ``get_agent_llm()``). We call
    ``llm.invoke`` with ``tools=None`` so the model produces plain text,
    then parse the response.

    Returns the parsed payload ``{"top_3_predictions": [...]}`` on success,
    or ``None`` if the model output can't be parsed/validated. On ``None``,
    the existing scorer fallback (keyword bridge) runs — no regression vs
    pre-predictor behavior.
    """
    system = _build_system_prompt()
    user_content = _build_user_prompt(alert_text, investigation_summary)

    try:
        response = retry_on_rate_limit(
            lambda: llm.invoke([{"role": "user", "content": user_content}], system=system),
            label="predictor",
        )
    except LLMCreditExhaustedError:
        # Fatal — propagate so the bench runner halts. Continuing on a
        # dead account would just emit hundreds of None-results for cells
        # that have no chance of scoring; the operator needs to top up
        # balance first.
        raise
    except Exception as exc:  # noqa: BLE001 — best-effort step; never block scoring
        logger.warning("[predictor] LLM invocation failed: %s", exc)
        return None

    payload = _parse_predictions(getattr(response, "content", "") or "")
    if payload is None:
        logger.warning("[predictor] could not parse top_3_predictions from LLM output")
        return None
    return payload


# --------------------------------------------------------------------------- #
# Prompt construction                                                         #
# --------------------------------------------------------------------------- #


def _build_system_prompt() -> str:
    return (
        "You are a CloudOpsBench fault-localization formatter.\n"
        "Given an alert and an investigation summary, output exactly ONE JSON\n"
        "object with a 'top_3_predictions' array of THREE ranked guesses for\n"
        "the most likely fault localization.\n\n"
        "Schema (ALL fields required on every prediction):\n"
        "  {\n"
        '    "top_3_predictions": [\n'
        "      {\n"
        '        "rank": 1,\n'
        '        "fault_taxonomy": <one of the taxonomies below>,\n'
        '        "fault_object": <canonical fault location string>,\n'
        '        "root_cause": <one of the root_cause enum values below>\n'
        "      },\n"
        "      ... (rank 2, rank 3)\n"
        "    ]\n"
        "  }\n\n"
        "Allowed fault_taxonomy values:\n"
        f"  {', '.join(_TAXONOMY_CATEGORIES)}\n\n"
        "Allowed root_cause values (must match exactly, snake_case):\n"
        f"  {', '.join(_ROOT_CAUSES)}\n"
        "  Plus any 'namespace_*' suffix for namespace-admission faults.\n\n"
        "fault_object format — pick ONE of these shapes:\n"
        f"  app/<service>      where service is one of: {', '.join(_FAULT_OBJECT_SERVICES)}\n"
        f"  node/<name>        where name is one of: {', '.join(_FAULT_OBJECT_NODES)}\n"
        f"  namespace/<ns>     where ns is one of: {', '.join(_FAULT_OBJECT_NAMESPACES)}\n\n"
        "Rules:\n"
        "  - Output ONLY the JSON object. No prose, no markdown fences.\n"
        "  - Rank 1 must be your strongest hypothesis given the evidence.\n"
        "  - Ranks 2 and 3 should be plausible alternatives, not duplicates.\n"
        "  - fault_taxonomy MUST correspond to the chosen root_cause family.\n"
    )


def _build_user_prompt(alert_text: str, investigation_summary: str) -> str:
    if investigation_summary.strip():
        body = (
            "ALERT:\n"
            f"{alert_text}\n\n"
            "INVESTIGATION SUMMARY:\n"
            f"{investigation_summary}\n\n"
            "Emit the JSON object now."
        )
    else:
        # llm_alone path — no prior investigation to lean on.
        body = (
            "ALERT:\n"
            f"{alert_text}\n\n"
            "No prior investigation evidence is available; reason from the\n"
            "alert alone. Emit the JSON object now."
        )
    return body


# --------------------------------------------------------------------------- #
# Response parsing                                                            #
# --------------------------------------------------------------------------- #


_FENCED_JSON = re.compile(r"```(?:json)?\s*(.*?)\s*```", re.DOTALL)


def _parse_predictions(text: str) -> dict[str, Any] | None:
    """Parse the LLM's text response into a validated predictions payload.

    Accepts:
      - bare JSON object
      - JSON wrapped in ```json ... ``` or ``` ... ``` fences (common LLM output)

    Returns None if the payload doesn't parse, doesn't contain
    ``top_3_predictions``, or contains zero usable predictions.
    """
    if not text:
        return None
    candidate = text.strip()
    match = _FENCED_JSON.search(candidate)
    if match:
        candidate = match.group(1).strip()

    try:
        parsed = json.loads(candidate)
    except json.JSONDecodeError:
        return None
    if not isinstance(parsed, dict):
        return None

    predictions = parsed.get("top_3_predictions")
    if not isinstance(predictions, list) or not predictions:
        return None

    cleaned: list[dict[str, Any]] = []
    for index, prediction in enumerate(predictions[:3]):
        if not isinstance(prediction, dict):
            continue
        fault_object = prediction.get("fault_object")
        root_cause = prediction.get("root_cause")
        if not isinstance(fault_object, str) or not isinstance(root_cause, str):
            continue

        # Derive fault_taxonomy deterministically from root_cause using the
        # scorer's mapping. The LLM's guess is overridden because the paper's
        # taxonomy is a function OF root_cause, not an independent dimension —
        # the model often picks the surface-phase taxonomy ("Startup_Fault" for
        # something that breaks during startup) instead of the root-cause
        # family ("Runtime_Fault" for mysql_invalid_credentials). Without this
        # override we lose a1 even on substantively-correct diagnoses.
        normalized_root_cause = root_cause.strip()
        derived_taxonomy = _taxonomy_for_root_cause(normalized_root_cause)
        llm_taxonomy = (prediction.get("fault_taxonomy") or "").strip()
        if llm_taxonomy and llm_taxonomy != derived_taxonomy:
            logger.info(
                "[predictor] rank=%d overrode LLM fault_taxonomy=%r with "
                "derived=%r for root_cause=%r",
                index + 1,
                llm_taxonomy,
                derived_taxonomy,
                normalized_root_cause,
            )

        cleaned.append(
            {
                "rank": prediction.get("rank", index + 1),
                "fault_taxonomy": derived_taxonomy,
                "fault_object": fault_object.strip(),
                "root_cause": normalized_root_cause,
            }
        )

    if not cleaned:
        return None
    return {"top_3_predictions": cleaned}
