"""Grafana alert-source detection heuristic.

Grafana managed alerts reuse the Alertmanager webhook schema verbatim, so
``alert_source`` is often missing from the payload. This detector sniffs
``grafana_folder`` / ``datasource_uid`` labels and ``externalURL`` to
recognize a Grafana-origin alert. Registered with
:func:`core.domain.alerts.alert_source.register_alert_source_detector` from
``integrations/harness_adapters.py``.
"""

from __future__ import annotations

from typing import Any


def detect_grafana_alert_source(raw: dict[str, Any]) -> str | None:
    """Return ``"grafana"`` when ``raw`` looks like a Grafana managed alert."""
    labels = raw.get("commonLabels") or raw.get("labels") or {}
    if isinstance(labels, dict) and (labels.get("grafana_folder") or labels.get("datasource_uid")):
        return "grafana"
    ext_url = raw.get("externalURL", "")
    if isinstance(ext_url, str) and "grafana" in ext_url.lower():
        return "grafana"
    return None


__all__ = ["detect_grafana_alert_source"]
