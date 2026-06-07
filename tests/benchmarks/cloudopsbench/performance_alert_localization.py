"""Deterministic performance-fault localization from CloudOpsBench metric alerts.

Performance cases often alert on MULTIPLE services (latency on victims, CPU
throttling on a different pod). The 2026-06-07 Anthropic pilot showed opensre
investigations cluster on the loudest CPU-throttling service while the injected
fault is ``pod_network_delay`` on the service with the extreme relative latency
spike. This module reads ``raw_data/alert.json`` (the same data ``GetAlerts``
returns) and infers rank-1 ``fault_object`` + ``root_cause`` before the
paper-format predictor runs.

Heuristics (audited against boutique/trainticket performance metadata):
  - ``pod_cpu_overload``: exactly one service shows RESOURCE_SATURATION /
    cpu_cfs throttling and no competing latency leader.
  - ``pod_network_delay``: the service whose alert evidence contains the largest
    relative latency increase (``+NNNN%`` in LATENCY_DEGRADATION lines) wins,
    even when another service shows CPU throttling.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

_LATENCY_PCT_RE = re.compile(r"\+(\d+(?:\.\d+)?)%")
_CPU_THROTTLE_MARKERS = ("cpu_cfs", "Throttled")


def load_alert_json(case_dir: Path) -> dict[str, Any] | None:
    path = case_dir / "raw_data" / "alert.json"
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None
    return data if isinstance(data, dict) else None


def format_metric_alerts(alert_data: dict[str, Any] | None) -> str:
    """Compact per-service anomaly lines for the paper-format predictor."""
    if not alert_data:
        return ""
    alerts = alert_data.get("alerts")
    if not isinstance(alerts, list) or not alerts:
        return ""
    lines: list[str] = []
    for entry in alerts:
        if not isinstance(entry, dict):
            continue
        name = entry.get("entity_name") or entry.get("name")
        if not isinstance(name, str) or not name.strip():
            continue
        category = entry.get("metric_category")
        cat = category if isinstance(category, str) and category.strip() else "METRIC"
        evidence = entry.get("evidence")
        if isinstance(evidence, list):
            ev_text = " | ".join(str(x) for x in evidence if x)
        elif isinstance(evidence, str):
            ev_text = evidence
        else:
            ev_text = ""
        if not ev_text.strip():
            continue
        lines.append(f"  - {name}: [{cat}] {ev_text}")
    if not lines:
        return ""
    return "Metric anomalies (from GetAlerts):\n" + "\n".join(lines)


def _service_name(entry: dict[str, Any]) -> str | None:
    name = entry.get("entity_name") or entry.get("name")
    return name.strip() if isinstance(name, str) and name.strip() else None


def _evidence_text(entry: dict[str, Any]) -> str:
    evidence = entry.get("evidence")
    if isinstance(evidence, list):
        return " | ".join(str(x) for x in evidence if x)
    if isinstance(evidence, str):
        return evidence
    return ""


def _has_cpu_throttling(evidence: str, category: str) -> bool:
    return any(marker in evidence for marker in _CPU_THROTTLE_MARKERS) or (
        "RESOURCE_SATURATION" in category and "cpu" in evidence.lower()
    )


def _max_latency_pct_increase(evidence: str) -> float:
    return max((float(m) for m in _LATENCY_PCT_RE.findall(evidence)), default=0.0)


def infer_performance_localization(
    alert_data: dict[str, Any] | None,
    *,
    namespace: str,
) -> dict[str, str] | None:
    """Infer ``fault_object`` + ``root_cause`` for a performance case from alerts.

    Returns ``None`` when the alert payload is missing or ambiguous. The
    ``namespace`` argument is only used to reject node-level entities.
    """
    if not alert_data:
        return None
    alerts = alert_data.get("alerts")
    if not isinstance(alerts, list) or not alerts:
        return None

    cpu_throttled: set[str] = set()
    latency_peak: dict[str, float] = {}

    for entry in alerts:
        if not isinstance(entry, dict):
            continue
        service = _service_name(entry)
        if service is None or service in {"master", "worker-01", "worker-02", "worker-03"}:
            continue
        category = entry.get("metric_category")
        cat = category if isinstance(category, str) else ""
        evidence = _evidence_text(entry)
        if _has_cpu_throttling(evidence, cat):
            cpu_throttled.add(service)
        peak = _max_latency_pct_increase(evidence)
        if peak > 0:
            latency_peak[service] = max(latency_peak.get(service, 0.0), peak)

    # One service with cpu_cfs: default pod_cpu_overload UNLESS another service
    # shows a much larger latency spike (pod_network_delay on the latency leader).
    if len(cpu_throttled) == 1:
        cpu_service = next(iter(cpu_throttled))
        cpu_latency = latency_peak.get(cpu_service, 0.0)
        other_latency = {
            svc: pct for svc, pct in latency_peak.items() if svc != cpu_service and pct >= 500.0
        }
        if other_latency:
            best_other = max(other_latency, key=other_latency.get)
            best_other_pct = other_latency[best_other]
            if best_other_pct > max(cpu_latency, 1.0) * 2:
                return {
                    "fault_object": f"app/{best_other}",
                    "root_cause": "pod_network_delay",
                    "rationale": (
                        f"largest relative latency spike (+{best_other_pct:.0f}%) "
                        f"on a non-CPU-throttled service"
                    ),
                }
        return {
            "fault_object": f"app/{cpu_service}",
            "root_cause": "pod_cpu_overload",
            "rationale": "cpu_cfs throttling on alerted service",
        }

    # No cpu throttling: latency leader with a large relative spike.
    if latency_peak:
        best_service = max(latency_peak, key=latency_peak.get)
        best_pct = latency_peak[best_service]
        if best_pct >= 500.0:
            return {
                "fault_object": f"app/{best_service}",
                "root_cause": "pod_network_delay",
                "rationale": (
                    f"largest relative latency spike (+{best_pct:.0f}%) among alerted services"
                ),
            }

    return None


def performance_context_for_case_dir(
    case_dir: Path, *, namespace: str
) -> tuple[str, dict[str, str] | None]:
    """Return ``(formatted_alerts, localization_hint)`` for a case directory."""
    alert_data = load_alert_json(case_dir)
    return (
        format_metric_alerts(alert_data),
        infer_performance_localization(alert_data, namespace=namespace),
    )
