"""Host-side ORCA pricing adapter.

Keeping benchmark-specific pricing outside the installed OpenSRE runner prevents
the runtime package from depending on the ORCA repository.
"""

from __future__ import annotations


def calculate_orca_cost(
    model: str,
    *,
    usage_events: list[dict[str, object]],
) -> float | None:
    """Calculate per-call cost with the pricing implementation pinned by ORCA."""
    from harbor_utils.token_utils import calculate_cost

    costs: list[float] = []
    for event in usage_events:
        pricing_models = dict.fromkeys(
            str(candidate)
            for candidate in (
                event.get("response_model"),
                event.get("requested_model"),
                model,
            )
            if candidate
        )
        cost = None
        for pricing_model in pricing_models:
            cost = calculate_cost(
                pricing_model,
                prompt_tokens=int(event.get("input_tokens") or 0),
                completion_tokens=int(event.get("output_tokens") or 0),
                cached_tokens=int(event.get("cache_read_tokens") or 0),
                cache_creation_5m_tokens=int(event.get("cache_creation_tokens") or 0),
            )
            if cost is not None:
                break
        if cost is None:
            return None
        costs.append(cost)
    return sum(costs) if costs else None
