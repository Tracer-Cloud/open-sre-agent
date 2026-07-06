"""Resolve integrations node — discovers which integrations are available for this alert."""

from __future__ import annotations

from typing import Any

from core.agent_harness.integrations.resolution import resolve_integrations_with_metadata
from core.state import InvestigationState
from platform.observability import get_progress_tracker as get_tracker


def resolve_integrations(state: InvestigationState) -> dict[str, Any]:
    """Discover and classify all integrations available for this investigation.

    Reads  : _auth_token, org_id, resolved_integrations (idempotency guard)
    Writes : resolved_integrations
    """
    return {"resolved_integrations": _resolve(state, emit_progress=True)}


def resolve_integrations_quiet(state: InvestigationState) -> dict[str, Any]:
    """Like :func:`resolve_integrations` but without progress-tracker UI."""
    return _resolve(state, emit_progress=False)


def _resolve(state: InvestigationState, *, emit_progress: bool) -> dict[str, Any]:
    """Return the raw integrations dict (keyed by vendor name)."""
    if state.get("resolved_integrations"):
        return dict(state["resolved_integrations"])

    tracker = get_tracker() if emit_progress else None
    if tracker is not None:
        tracker.start("resolve_integrations", "Fetching org integrations")

    result = resolve_integrations_with_metadata(state)
    _complete_tracker(
        tracker,
        "resolve_integrations",
        fields_updated=["resolved_integrations"],
        message=result.progress_message,
    )
    return result.resolved_integrations


def _complete_tracker(tracker: Any | None, node_name: str, **kwargs: Any) -> None:
    if kwargs.get("message") is None:
        kwargs.pop("message", None)
    if tracker is not None:
        tracker.complete(node_name, **kwargs)
