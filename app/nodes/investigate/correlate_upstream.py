from __future__ import annotations

from typing import Any

from app.state import InvestigationState


def _empty_correlation() -> dict[str, list[dict[str, Any]]]:
    return {
        "correlated_signals": [],
        "most_likely_causal_drivers": [],
    }


def node_correlate_upstream(
    state: InvestigationState,
    config: Any | None = None,
) -> dict[str, Any]:
    """Attach upstream-correlation payload to investigation state."""
    _ = config

    existing = state.get("correlation")
    if isinstance(existing, dict):
        correlated_signals = existing.get("correlated_signals")
        causal_drivers = existing.get("most_likely_causal_drivers")

        if isinstance(correlated_signals, list) and isinstance(causal_drivers, list):
            return {"correlation": existing}

    return {"correlation": _empty_correlation()}
