"""State constructors and defaults."""

from __future__ import annotations

import time
from typing import Any, cast

from core.domain.alerts.fields import (
    alert_annotations,
    alert_labels,
    alert_name_value,
    canonical_alert,
    first_text,
    severity_value,
)
from core.domain.alerts.normalization import normalize_alert_payload
from core.state.models import AgentState, AgentStateModel, model_default_payload
from integrations.opensre.hf_remote import (
    extract_scoring_points,
    strip_scoring_points_from_alert,
)


def make_initial_state(
    raw_alert: str | dict[str, Any],
    *,
    opensre_evaluate: bool = False,
    investigation_metadata: tuple[str, str] | None = None,
) -> AgentState:
    """Create initial investigation state from the raw alert payload.

    When ``investigation_metadata`` is set, it supplies ``(alert_name, severity)``
    for initial state instead of deriving them only from ``raw_alert``.
    """
    rubric = ""
    alert_payload: str | dict[str, Any] = raw_alert
    if isinstance(alert_payload, dict):
        if opensre_evaluate:
            rubric = extract_scoring_points(alert_payload)
            if rubric:
                alert_payload = strip_scoring_points_from_alert(dict(alert_payload))
        elif extract_scoring_points(alert_payload):
            # Blind investigation: drop rubric from agent-visible alert (file may include it).
            alert_payload = strip_scoring_points_from_alert(dict(alert_payload))

        # Normalize source-specific payloads into a canonical alert shape once,
        # before any downstream extraction/planning nodes run.
        alert_payload = normalize_alert_payload(alert_payload)

    if investigation_metadata is not None:
        alert_name, severity = investigation_metadata
    else:
        alert_name, severity = _resolve_alert_metadata(alert_payload)

    state = AgentStateModel.model_validate(
        {
            **model_default_payload("mode", "messages"),
            "mode": "investigation",
            "alert_name": alert_name,
            "severity": severity,
            "raw_alert": alert_payload,
            "investigation_started_at": time.monotonic(),
            "opensre_evaluate": opensre_evaluate,
            "opensre_eval_rubric": rubric,
        }
    )
    return cast(AgentState, state.model_dump(mode="python", by_alias=True, exclude_none=True))


def _resolve_alert_metadata(raw_alert: str | dict[str, Any]) -> tuple[str, str]:
    """Best-effort ``(alert_name, severity)`` until ``extract_alert`` parses deeper."""
    if not isinstance(raw_alert, dict):
        return ("Incident", "warning")

    labels = alert_labels(raw_alert)
    annotations = alert_annotations(raw_alert)
    canonical = canonical_alert(raw_alert)

    alert_name = first_text(
        alert_name_value(
            raw_alert,
            labels=labels,
            annotations=annotations,
            canonical=canonical,
        ),
        default="Incident",
    )
    severity = first_text(
        severity_value(raw_alert, labels=labels, canonical=canonical),
        default="warning",
    )
    return alert_name, severity
