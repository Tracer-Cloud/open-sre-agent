"""Block false-healthy conclusions when tool observations disagree.

Path B (2026-06-07): the opensre+llm arm sometimes declares the cluster
healthy while ``GetResources`` output the agent already fetched shows
CrashLoopBackOff, ImagePullBackOff, Pending, or not-ready pods. The
control arm is unchanged — this guard lives on :class:`BenchInvestigationAgent`
only.

Design choices (per review):
  - Reads **investigation evidence** (``evidence_entries`` from tool calls),
    not the replay backend ground truth — generalizes beyond the bench.
  - **Downgrades** to unresolved rather than nudging the LLM to keep looping.
"""

from __future__ import annotations

import json
import re
from typing import Any

# kubectl STATUS column values that UNAMBIGUOUSLY indicate workload failure.
# Deliberately conservative: dropped ``error`` (matches "ERRORS: 0" /
# "no errors found"), ``failed`` (matches "0 failed pods"), and the lifecycle
# states ``pending`` / ``containercreating`` / ``terminating`` (normal during
# rolling deploys). The ``_POD_NOT_READY_LINE`` regex below catches genuinely
# stuck workloads via their ``0/N ready`` count, which is the load-bearing
# detection for those cases.
_UNHEALTHY_STATUS_TOKENS: tuple[str, ...] = (
    "crashloopbackoff",
    "imagepullbackoff",
    "errimagepull",
    "invalidimagename",
    "oomkilled",
    "notready",
)

# Pod list lines where READY is 0/N (workload not ready). Combined with the
# token list above, this is what catches Pending / Failed / ContainerCreating
# pods that are actually stuck (vs transient during a normal rollout).
_POD_NOT_READY_LINE = re.compile(
    r"^[a-z0-9][-a-z0-9.]*\s+0/\d+\s+",
    re.IGNORECASE | re.MULTILINE,
)

# Phrases used to recognize a false-healthy investigation conclusion in the
# free-text root_cause / report. Each entry MUST be specific enough that it
# cannot also appear inside a correct unhealthy diagnosis. In particular,
# ``"all pods are healthy"`` is the load-bearing phrase here, not ``"all pods
# in"`` (which would match e.g. ``"all pods in the boutique namespace are in
# CrashLoopBackOff"`` — a correct unhealthy diagnosis the guard would then
# wrongly downgrade).
_FALSE_HEALTHY_PHRASES: tuple[str, ...] = (
    "cluster appears healthy",
    "appears healthy as no active",
    "appears healthy as no",
    "all pods are healthy",
    "all pods in the cluster are healthy",
    "all pods in the namespace are healthy",
    "false positive",
    "no active anomalies",
    "no failure signs",
    "insufficient evidence to determine a specific root cause",
    "insufficient evidence to determine",
)

_DOWNGRADE_ROOT_CAUSE = (
    "Investigation concluded the environment is healthy, but prior tool "
    "observations show unhealthy or not-ready workloads. Marked unresolved — "
    "requires further human review."
)


def evidence_shows_unhealthy_workloads(evidence_entries: list[dict[str, Any]]) -> bool:
    """Return True when any recorded ``GetResources`` observation shows failure."""
    for entry in evidence_entries:
        tool_name = str(entry.get("tool_name") or entry.get("key") or "")
        if tool_name != "GetResources":
            continue
        args = entry.get("tool_args") or {}
        resource_type = str(args.get("resource_type") or "").strip().lower()
        if resource_type not in {"", "pod", "pods"}:
            continue
        text = _extract_observation_text(entry.get("data")).lower()
        if not text:
            continue
        if any(token in text for token in _UNHEALTHY_STATUS_TOKENS):
            return True
        if _POD_NOT_READY_LINE.search(text):
            return True
    return False


def investigation_declares_healthy(updates: dict[str, Any]) -> bool:
    """True when the parsed investigation claims health / no actionable fault."""
    category = str(updates.get("root_cause_category") or "").strip().lower()
    if category == "healthy":
        return True
    blob = f"{updates.get('root_cause', '')}\n{updates.get('report', '')}".lower()
    return any(phrase in blob for phrase in _FALSE_HEALTHY_PHRASES)


def should_downgrade_false_healthy(updates: dict[str, Any]) -> bool:
    """Downgrade when the investigation says healthy but evidence disagrees."""
    if not investigation_declares_healthy(updates):
        return False
    entries = updates.get("evidence_entries") or []
    if not isinstance(entries, list):
        return False
    return evidence_shows_unhealthy_workloads(entries)


def apply_false_healthy_downgrade(updates: dict[str, Any]) -> dict[str, Any]:
    """Replace a false-healthy conclusion with an explicit unresolved outcome."""
    downgraded = dict(updates)
    downgraded["root_cause_category"] = "unknown"
    downgraded["root_cause"] = _DOWNGRADE_ROOT_CAUSE
    prior_report = str(updates.get("report") or "").strip()
    note = (
        "## Investigation note\n"
        "The prior conclusion that the cluster is healthy was rejected because "
        "tool observations recorded unhealthy or not-ready pod statuses."
    )
    downgraded["report"] = f"{prior_report}\n\n{note}".strip() if prior_report else note
    return downgraded


def _extract_observation_text(data: Any) -> str:
    """Normalize tool output stored on an evidence entry to searchable text."""
    if data is None:
        return ""
    if isinstance(data, str):
        return data
    if isinstance(data, dict):
        for key in ("output", "content", "text", "message"):
            if key in data:
                return _extract_observation_text(data[key])
        if data.get("available") is False:
            return ""
        return json.dumps(data, ensure_ascii=False)
    return str(data)
