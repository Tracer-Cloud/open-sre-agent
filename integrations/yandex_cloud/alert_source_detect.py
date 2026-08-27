"""Yandex Monitoring alert-source detection.

Yandex Monitoring has no plain webhook channel. Alerts leave it by Email, SMS,
push, Telegram or a Cloud Function, so the payload that reaches OpenSRE is
whatever the operator's function forwards — there is no single published schema
to match on. Detection therefore looks for markers that only a Yandex Cloud
alert carries, and stays tolerant about everything else.

Registered with :func:`core.domain.alerts.alert_source.register_alert_source_detector`
from ``integrations/harness_adapters.py``, so core keeps no vendor heuristics.
"""

from __future__ import annotations

from typing import Any

SOURCE = "yandex_monitoring"

#: Field names that only appear on a Yandex Cloud payload. Both spellings are
#: listed because the REST API answers in camelCase while the CLI and most
#: hand-written functions emit snake_case.
_IDENTIFYING_KEYS: tuple[str, ...] = (
    "folderId",
    "folder_id",
    "cloudId",
    "cloud_id",
    "alertId",
    "alert_id",
    "evaluationStatus",
    "evaluation_status",
)

#: An alert-shaped payload has at least one of these beside the identifier.
_ALERT_SHAPED_KEYS: tuple[str, ...] = (
    "alertId",
    "alert_id",
    "alertName",
    "alert_name",
    "evaluationStatus",
    "evaluation_status",
    "status",
    "state",
    "severity",
    "threshold",
)

_HOST_MARKERS: tuple[str, ...] = ("yandexcloud.net", "yandex.cloud", "monitoring.yandex")


def _blocks(raw: dict[str, Any]) -> list[dict[str, Any]]:
    """The payload plus the nested mappings an operator's function may nest under."""
    blocks = [raw]
    for key in ("alert", "labels", "commonLabels", "annotations", "commonAnnotations", "data"):
        nested = raw.get(key)
        if isinstance(nested, dict):
            blocks.append(nested)
    return blocks


def detect_yandex_cloud_alert_source(raw: dict[str, Any]) -> str | None:
    """Return ``"yandex_monitoring"`` when *raw* looks like a Yandex Cloud alert.

    Requires a Yandex identifier *and* something alert-shaped, so a payload that
    merely mentions a folder — a routine tool result forwarded by hand, say — is
    not mistaken for a firing alert. A Yandex Cloud URL is accepted on its own:
    nothing else produces one.
    """
    blocks = _blocks(raw)

    for block in blocks:
        for value in block.values():
            if isinstance(value, str) and any(marker in value for marker in _HOST_MARKERS):
                return SOURCE

    has_identifier = any(key in block for block in blocks for key in _IDENTIFYING_KEYS)
    if not has_identifier:
        return None
    if any(key in block for block in blocks for key in _ALERT_SHAPED_KEYS):
        return SOURCE
    return None


__all__ = ["SOURCE", "detect_yandex_cloud_alert_source"]
