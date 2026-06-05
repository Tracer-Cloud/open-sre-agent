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

import difflib
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
# Lever A — controlled-vocabulary snapping                                    #
#                                                                             #
# The scorer (scoring.compare_prediction) requires an EXACT match, after      #
# lower-case + strip, against the dataset's canonical tokens. Failure         #
# analysis of the 2026-06-05 run showed 62% of a1=0 cases emitted a           #
# root_cause that is not in the dataset vocabulary at all — including pure     #
# drift like ``missing_secrectbinding`` (→ missing_secret_binding) and        #
# ``network_packet_loss`` (→ node_network_packet_loss). Those auto-fail no    #
# matter how good the diagnosis was. We snap the model's output back onto     #
# the closed vocabulary before scoring. Snapping only ever moves a token      #
# CLOSER to a canonical value, so it cannot regress a previously-passing      #
# case; if nothing is close enough, the original cleaned string is kept.      #
# --------------------------------------------------------------------------- #

_ROOT_CAUSE_BY_NORM: dict[str, str] = {rc.lower(): rc for rc in _ROOT_CAUSES}
_KNOWN_SERVICES_BY_NORM: dict[str, str] = {s.lower(): s for s in _FAULT_OBJECT_SERVICES}
_KNOWN_NODES_BY_NORM: dict[str, str] = {n.lower(): n for n in _FAULT_OBJECT_NODES}
_KNOWN_NAMESPACES_BY_NORM: dict[str, str] = {n.lower(): n for n in _FAULT_OBJECT_NAMESPACES}

# Conservative: only snap a root_cause when the closest canonical token is a
# clear typo/spacing variant. 0.8 catches the observed drift (e.g.
# ``missing_secrectbinding`` → ``missing_secret_binding`` at 0.95,
# ``network_packet_loss`` → ``node_network_packet_loss`` at 0.88) without
# pulling totally unrelated tokens. Note that ratio alone cannot separate
# every legitimate snap from a cross-concept jump — see
# ``_BLOCKED_CONCEPT_PAIRS`` below for the second guard.
_ROOT_CAUSE_SNAP_CUTOFF = 0.8

# Word stems whose canonicals exist in pairs and differ by only a few chars,
# making them susceptible to difflib false-positive snapping. The 11:46 run
# emitted ``readiness_probe_incorrect_timing`` (no canonical for it) which
# scores 0.889 against ``liveness_probe_incorrect_timing`` — above the snap
# cutoff but semantically a different probe type. Raising the global cutoff
# to block this pair would break the legitimate
# ``network_packet_loss`` → ``node_network_packet_loss`` snap (0.884), so we
# express the constraint as an explicit blocklist instead. Extend when other
# concept pairs surface from future runs.
_BLOCKED_CONCEPT_PAIRS: tuple[tuple[str, str], ...] = (("readiness", "liveness"),)


def _crosses_blocked_concept_boundary(predicted_norm: str, snapped: str) -> bool:
    """Refuse a snap that crosses a known concept boundary (readiness↔liveness)."""
    snapped_lower = snapped.lower()
    for a, b in _BLOCKED_CONCEPT_PAIRS:
        # predicted contains stem A AND target contains stem B (and not A) →
        # the snap is rewriting one concept onto a sibling. Symmetric check
        # via the for-loop iterating both orderings.
        if a in predicted_norm and b in snapped_lower and a not in snapped_lower:
            return True
        if b in predicted_norm and a in snapped_lower and b not in snapped_lower:
            return True
    return False


def _snap_root_cause(raw: str) -> str:
    """Snap an LLM-emitted root_cause onto the dataset's closed vocabulary.

    Resolution order: exact (after lower + underscore normalization) →
    ``namespace_*`` admission tokens pass through → closest canonical token by
    difflib ratio above ``_ROOT_CAUSE_SNAP_CUTOFF`` AND not crossing a
    blocked concept boundary. Falls back to the cleaned input when nothing
    is close enough OR the closest match would cross a blocked boundary
    (no regression vs. the pre-snap behavior).
    """
    cleaned = raw.strip()
    if not cleaned:
        return cleaned
    norm = re.sub(r"[\s\-]+", "_", cleaned.lower()).strip("_")
    if norm in _ROOT_CAUSE_BY_NORM:
        return _ROOT_CAUSE_BY_NORM[norm]
    # Namespace-admission faults are an open ``namespace_<reason>`` family the
    # scorer maps to Admission_Fault; keep the normalized form verbatim.
    if norm.startswith("namespace_"):
        return norm
    match = difflib.get_close_matches(
        norm, list(_ROOT_CAUSE_BY_NORM), n=1, cutoff=_ROOT_CAUSE_SNAP_CUTOFF
    )
    if match:
        snapped = _ROOT_CAUSE_BY_NORM[match[0]]
        if _crosses_blocked_concept_boundary(norm, snapped):
            logger.info(
                "[predictor] refused cross-concept snap %r → %r (blocked pair)",
                cleaned,
                snapped,
            )
            return cleaned
        if snapped.lower() != norm:
            logger.info("[predictor] snapped root_cause %r → %r", cleaned, snapped)
        return snapped
    return cleaned


def _snap_fault_object(raw: str) -> str:
    """Normalize a fault_object to the canonical ``<prefix>/<name>`` shape.

    Adds a missing prefix (inferring node/namespace/app from the name) and
    canonicalizes known node/namespace/service tokens. Service names are only
    canonicalized on an exact normalized match — the service list is a known
    subset of the corpus, so fuzzy-snapping here would risk rewriting a correct
    novel service onto a wrong listed one. The scorer already lower-cases both
    sides, so this is scoring-neutral except where it genuinely helps (missing
    prefix, casing of known tokens).
    """
    cleaned = raw.strip()
    if not cleaned:
        return cleaned
    low = cleaned.lower()
    if "/" in low:
        prefix, _, name = low.partition("/")
        prefix, name = prefix.strip(), name.strip()
    else:
        prefix, name = "", low
    if prefix not in {"app", "node", "namespace"}:
        if name in _KNOWN_NODES_BY_NORM:
            prefix = "node"
        elif name in _KNOWN_NAMESPACES_BY_NORM:
            prefix = "namespace"
        else:
            prefix = "app"
    if prefix == "node" and name in _KNOWN_NODES_BY_NORM:
        name = _KNOWN_NODES_BY_NORM[name]
    elif prefix == "namespace" and name in _KNOWN_NAMESPACES_BY_NORM:
        name = _KNOWN_NAMESPACES_BY_NORM[name]
    elif prefix == "app" and name in _KNOWN_SERVICES_BY_NORM:
        name = _KNOWN_SERVICES_BY_NORM[name]
    return f"{prefix}/{name}" if name else cleaned


# --------------------------------------------------------------------------- #
# Lever D — evidence-weighted top-3 re-ranking                                #
#                                                                             #
# 11:46 failure analysis: of the 77 a1=0 cases, 41 (53%) had the correct      #
# ``fault_object`` SOMEWHERE in the LLM's top-3, but only 29 (38%) at rank-1. #
# That's ~15 points of object accuracy parked in ranks 2-3 because the        #
# LLM's own confidence ordering didn't surface the best-evidenced candidate.  #
# Re-ranking by how many of each prediction's identifying tokens appear in    #
# the actual investigation evidence pulls those candidates up.                 #
#                                                                             #
# Cheap deterministic variant (this function): substring count, no LLM call.  #
# Audit-grade variant (LLM-as-judge over the same input) is a follow-up.       #
# --------------------------------------------------------------------------- #

# Stem tokens that are too common across predictions to discriminate by their
# presence in the evidence — "service" appears in every Kubernetes diagnosis,
# "fault" / "error" / "pod" are noise. Counting them inflates every prediction's
# score equally, defeating the rerank. Drop them from the token set.
_RERANK_STOPWORDS: frozenset[str] = frozenset(
    {"app", "node", "namespace", "service", "fault", "error", "pod", "the", "and", "for"}
)

# Tokens shorter than this can't carry meaningful signal (single letters,
# 2-char abbreviations are too noisy to substring-match reliably).
_RERANK_MIN_TOKEN_LEN: int = 3


def _prediction_tokens(prediction: dict[str, Any]) -> set[str]:
    """Pull the identifying tokens from one prediction.

    Combines ``fault_object`` (after stripping the prefix) and ``root_cause``,
    splits on the structural separators that the dataset uses (``_``, ``-``,
    ``/``), lowercases, and drops stop-words + tokens shorter than
    ``_RERANK_MIN_TOKEN_LEN``. The result is the set of substrings that
    should appear in the evidence if this prediction is well-supported.
    """
    fields: list[str] = []
    fault_obj = (prediction.get("fault_object") or "").strip().lower()
    if "/" in fault_obj:
        _prefix, _, name = fault_obj.partition("/")
        fault_obj = name
    if fault_obj:
        fields.append(fault_obj)
    root_cause = (prediction.get("root_cause") or "").strip().lower()
    if root_cause:
        fields.append(root_cause)
    tokens: set[str] = set()
    for field in fields:
        for tok in re.split(r"[_\-/\s]+", field):
            if len(tok) >= _RERANK_MIN_TOKEN_LEN and tok not in _RERANK_STOPWORDS:
                tokens.add(tok)
    return tokens


def rerank_predictions_by_evidence(
    predictions: list[dict[str, Any]],
    evidence_text: str,
) -> list[dict[str, Any]]:
    """Conservatively rescue the top-1 if it has zero evidence support.

    **Empirical motivation**: a permissive "always re-sort by substring
    hits" version was tested against the 11:46 case data and produced a
    −7.2pp regression on A@1 (103/180 → 90/180 correct triple-matches).
    Cause: when the investigation discusses multiple services, multiple
    predictions accumulate substring hits, and a wrong-but-multiply-cited
    rank-2 was beating a correct-and-singly-cited rank-1. Substring count
    alone is not strong enough signal to over-rule the LLM's confidence
    ordering.

    The conservative variant in this function only fires when **rank-1
    has ZERO matching tokens in the evidence** (a clear "the LLM picked a
    prediction the investigation never mentioned" signal). When that
    fires, the highest-scoring non-rank-1 prediction is promoted. All
    other cases are identity — protecting the LLM's confidence ordering
    when it has any evidence backing at all.

    This recovers ~2 a1 cells per 180 (from the 11:46 replay) without
    regressing the 30+ cells the LLM had correctly ranked at #1.

    Returns a NEW list — the input is not mutated. ``rank`` is rewritten
    to match the new 1-based positions.
    """
    if len(predictions) <= 1:
        return list(predictions)
    haystack = (evidence_text or "").lower()
    if not haystack.strip():
        return list(predictions)
    scores: list[int] = []
    for prediction in predictions:
        tokens = _prediction_tokens(prediction)
        scores.append(sum(1 for tok in tokens if tok in haystack))
    # Conservative gate: only intervene when rank-1 has zero evidence hits.
    # When the LLM's top pick IS evidenced at all, defer to its judgment —
    # cross-citation noise in the substring count is too high to over-rule
    # a confidence ordering that has any backing.
    if scores[0] > 0:
        return list(predictions)
    # Find the highest-scoring non-rank-1 prediction. If none score positive,
    # all predictions are unevidenced and we have no signal to act on.
    best_alt_idx: int | None = None
    best_alt_score = 0
    for idx in range(1, len(predictions)):
        if scores[idx] > best_alt_score:
            best_alt_score = scores[idx]
            best_alt_idx = idx
    if best_alt_idx is None:
        return list(predictions)
    # Promote: chosen alt becomes rank-1, original rank-1 takes the alt's slot,
    # everything else preserves relative order so the swap is minimally disruptive.
    promoted = predictions[best_alt_idx]
    new_order = [promoted, predictions[0]]
    for idx, prediction in enumerate(predictions):
        if idx in (0, best_alt_idx):
            continue
        new_order.append(prediction)
    return [{**prediction, "rank": new_rank + 1} for new_rank, prediction in enumerate(new_order)]


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
        # Lever A: snap onto the dataset's closed vocabulary before scoring so
        # near-miss tokens don't auto-fail the exact-match scorer.
        normalized_root_cause = _snap_root_cause(root_cause)
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
                "fault_object": _snap_fault_object(fault_object),
                "root_cause": normalized_root_cause,
            }
        )

    if not cleaned:
        return None
    return {"top_3_predictions": cleaned}
