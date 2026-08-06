"""Host-side ORCA pricing adapter.

Keeping benchmark-specific pricing outside the installed OpenSRE runner prevents
the runtime package from depending on the ORCA repository.
"""

from __future__ import annotations


def calculate_orca_cost(model: str, *, input_tokens: int, output_tokens: int) -> float | None:
    """Calculate cost with the pricing implementation pinned by ORCA."""
    from harbor_utils.token_utils import calculate_cost

    return calculate_cost(
        model,
        prompt_tokens=input_tokens,
        completion_tokens=output_tokens,
    )
