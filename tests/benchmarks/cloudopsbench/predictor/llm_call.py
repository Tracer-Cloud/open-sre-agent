"""Predictor LLM call: investigation free-text → paper-format ``top_3_predictions`` JSON.

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

from core.runtime.llm.llm_retry import LLMCreditExhaustedError, retry_on_rate_limit
from tests.benchmarks.cloudopsbench.predictor.snapping import _snap_fault_object, _snap_root_cause
from tests.benchmarks.cloudopsbench.predictor.vocabulary import (
    _FAULT_OBJECT_NAMESPACES,
    _FAULT_OBJECT_NODES,
    _FAULT_OBJECT_SERVICES,
    _ROOT_CAUSES,
    _TAXONOMY_CATEGORIES,
)
from tests.benchmarks.cloudopsbench.taxonomy import taxonomy_for_root_cause

logger = logging.getLogger(__name__)


# --------------------------------------------------------------------------- #
# Public API                                                                  #
# --------------------------------------------------------------------------- #


def emit_paper_predictions(
    *,
    alert_text: str,
    investigation_summary: str,
    llm: Any,
    metric_alerts: str = "",
    performance_localization_hint: dict[str, str] | None = None,
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
    user_content = _build_user_prompt(
        alert_text,
        investigation_summary,
        metric_alerts=metric_alerts,
        performance_localization_hint=performance_localization_hint,
    )

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
    """Canonical predictor system prompt.

    Encodes the schema (top_3_predictions with rank / fault_taxonomy /
    fault_object / root_cause), the closed vocabularies from
    ``vocabulary.py`` interpolated inline so the model sees the exact
    enum surface the scorer compares against, the
    investigation-is-authoritative rule, the namespace-scope rule for
    admission faults, and the performance-fault disambiguation rules.
    Pure string — no per-call state, safe to cache via OpenAI's automatic
    prefix cache (≥1024-token stable prefix qualifies).
    """
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
        "  - If an INVESTIGATION SUMMARY is provided, it is the conclusion of a\n"
        "    tool-driven root-cause investigation. Treat it as AUTHORITATIVE:\n"
        "    rank 1 MUST be the schema-formalized version of the component and\n"
        "    root cause it identifies. Do NOT re-diagnose from the alert and\n"
        "    discard it — only deviate if the summary names no component or is\n"
        "    internally contradictory. (The scope rule below still applies when\n"
        "    choosing the fault_object level.)\n"
        "  - With NO investigation summary, rank 1 is your strongest hypothesis\n"
        "    reasoning from the alert alone.\n"
        "  - Ranks 2 and 3 should be plausible alternatives, not duplicates.\n"
        "  - fault_taxonomy MUST correspond to the chosen root_cause family.\n\n"
        "Scope rule (CRITICAL — the fault lives at the level it ORIGINATES, not\n"
        "where symptoms show up):\n"
        "  - If root_cause is any 'namespace_*' admission token (e.g.\n"
        "    'namespace_memory_quota_exceeded', 'namespace_cpu_quota_exceeded',\n"
        "    'namespace_pod_quota_exceeded'), fault_object MUST be\n"
        "    'namespace/<X>' — NEVER 'app/<service>'. Quota / admission faults\n"
        "    live at the namespace; individual services are downstream victims.\n"
        "  - If the evidence shows MULTIPLE services in the same namespace\n"
        "    failing together AND the cause is a namespace-level limit (quota,\n"
        "    service account, network policy, resource cap), the strongest\n"
        "    rank-1 hypothesis is 'namespace/<X>' even if one service appears\n"
        "    'first to fail'. A single-service prediction here is wrong scope.\n"
        "  - If the cause is genuinely an app-level misconfiguration (wrong\n"
        "    port, bad image reference, probe misconfig, missing secret binding\n"
        "    on ONE deployment), keep fault_object as 'app/<service>'. The\n"
        "    scope rule only fires for cross-service namespace-wide failures.\n\n"
        "Performance-fault disambiguation (when metric anomalies are present):\n"
        "  - ``pod_cpu_overload``: rank-1 ``fault_object`` is the service whose\n"
        "    alert shows RESOURCE_SATURATION / cpu_cfs throttling ON THAT SERVICE.\n"
        "  - ``pod_network_delay``: rank-1 ``fault_object`` is the service with\n"
        "    the largest relative LATENCY_DEGRADATION spike (highest +%% increase\n"
        "    in p50/p90), NOT a different service that only shows CPU throttling.\n"
        "    CPU throttling on service A does not localize ``pod_network_delay``\n"
        "    onto A when service B has the extreme latency spike.\n"
        "  - Do NOT emit ``namespace_*`` quota tokens on performance alerts unless\n"
        "    the investigation explicitly identifies a ResourceQuota admission block.\n"
        "  - When an ALERT-DERIVED PERFORMANCE LOCALIZATION block is provided in\n"
        "    the user message, it is AUTHORITATIVE for rank-1 ``fault_object`` and\n"
        "    ``root_cause`` — formalize it even if the investigation summary names\n"
        "    a different service or a cluster-wide CPU saturation narrative.\n"
    )


def _build_user_prompt(
    alert_text: str,
    investigation_summary: str,
    *,
    metric_alerts: str = "",
    performance_localization_hint: dict[str, str] | None = None,
) -> str:
    """Assemble the predictor user-message body for one case.

    Composes three optional blocks onto the alert text in fixed order so
    the prompt prefix above the variable section stays cacheable:

      1. ``ALERT:`` — required; alert text + appended ``metric_alerts``
         block when present (treated as continuation of the alert).
      2. ``ALERT-DERIVED PERFORMANCE LOCALIZATION`` — emitted only when
         ``performance_localization_hint`` is provided. Marked
         AUTHORITATIVE for rank-1 to override any cluster-wide
         narrative the investigation produced.
      3. ``INVESTIGATION SUMMARY`` — emitted when ``investigation_summary``
         is non-empty (opensre+llm path). Absent on the ``llm_alone``
         path so the model reasons from the alert alone.

    The closing instruction differs between the two paths: with a summary
    the model is told to formalize its conclusion; without, to reason
    from the alert. No model-state side effects — pure string assembly.
    """
    alert_block = alert_text.strip()
    if metric_alerts.strip():
        alert_block = (
            f"{alert_block}\n\n{metric_alerts.strip()}" if alert_block else metric_alerts.strip()
        )

    perf_block = ""
    if performance_localization_hint:
        perf_block = (
            "ALERT-DERIVED PERFORMANCE LOCALIZATION (AUTHORITATIVE for rank-1\n"
            "fault_object + root_cause — overrides cluster-wide investigation\n"
            "narratives that name the wrong service):\n"
            f"  fault_object: {performance_localization_hint.get('fault_object', '')}\n"
            f"  root_cause: {performance_localization_hint.get('root_cause', '')}\n"
            f"  rationale: {performance_localization_hint.get('rationale', '')}\n\n"
        )

    if investigation_summary.strip():
        body = (
            "ALERT:\n"
            f"{alert_block}\n\n"
            "INVESTIGATION SUMMARY (formalize its conclusion unless the performance\n"
            "localization block below overrides rank-1):\n"
            f"{investigation_summary}\n\n"
            f"{perf_block}"
            "Set rank 1 to the localized component and root cause (apply the scope\n"
            "rule for fault_object level). Emit the JSON object now."
        )
    else:
        # llm_alone path — no prior investigation to lean on.
        body = (
            "ALERT:\n"
            f"{alert_block}\n\n"
            f"{perf_block}"
            "No prior investigation evidence is available; reason from the\n"
            "alert and any performance localization block above. Emit the JSON\n"
            "object now."
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
        # Lever A: snap onto the dataset's closed vocabulary before scoring so
        # near-miss tokens don't auto-fail the exact-match scorer.
        normalized_root_cause = _snap_root_cause(root_cause)
        derived_taxonomy = taxonomy_for_root_cause(normalized_root_cause)
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
                "fault_object": _snap_fault_object(fault_object),
                "root_cause": normalized_root_cause,
            }
        )

    if not cleaned:
        return None
    return {"top_3_predictions": cleaned}
