"""Bridge investigation terminal outcomes to PostHog lifecycle analytics."""

from __future__ import annotations

from typing import Any

from platform.analytics.cli import (
    capture_investigation_cancelled,
    capture_investigation_outcome,
)
from surfaces.interactive_shell.ui.investigation_outcome import InvestigationOutcome


def _root_cause_excerpt(final_state: dict[str, Any] | None) -> str:
    if not final_state:
        return ""
    root = final_state.get("root_cause")
    if isinstance(root, str) and root.strip():
        return root.strip()
    return ""


def publish_investigation_outcome_analytics(outcome: InvestigationOutcome) -> None:
    """Emit structured investigation analytics for one terminal run."""
    if outcome.status == "cancelled":
        capture_investigation_cancelled(
            investigation_id=outcome.investigation_id,
            investigation_target=outcome.target,
        )
    capture_investigation_outcome(
        investigation_id=outcome.investigation_id,
        status=outcome.status,
        investigation_target=outcome.target,
        root_cause_excerpt=_root_cause_excerpt(outcome.final_state),
        error_excerpt=outcome.error_message,
        failure_category=outcome.failure_category or None,
        integration_involved=outcome.integration_involved or None,
        integration_failure_message=outcome.integration_failure_message or None,
        failure_detail=outcome.error_detail or None,
    )


__all__ = ["publish_investigation_outcome_analytics"]
