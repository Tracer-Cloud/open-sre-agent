"""Final output-token budget for every LLM client, on every transport.

`LLM_MAX_TOKENS` is an operator override layered over each provider's own
default. Every builder in :mod:`core.llm.client_builders` (native SDK, CLI) and
:mod:`core.llm.transports.litellm.routing` routes through
:func:`resolve_max_output_tokens`, so the override cannot be honoured on one
transport and silently dropped on another — the defect this module exists to
prevent.

The value is not clamped. Real ceilings are per-model, not per-provider, and an
over-limit value is rejected by the provider on the first request with a message
naming the parameter; a stale clamp table would instead swallow the setting.
"""

from __future__ import annotations

from typing import Any


def resolve_max_output_tokens(settings: Any, *, provider_default: int) -> int:
    """Return the output-token budget: the operator override, else *provider_default*.

    Args:
        settings: Resolved LLM settings. ``max_tokens`` is ``None`` when
            ``LLM_MAX_TOKENS`` is unset or blank, and validation has already
            rejected zero, negative, and non-numeric values upstream in
            ``LLMSettings``.
        provider_default: The budget this provider/tier ships today.

    Returns:
        The token budget to construct the client with.

    Note:
        ``getattr`` with a default is required, not defensive style: several
        test suites build settings fakes that omit ``max_tokens`` entirely, and
        a bare attribute access would raise. ``int(...)`` is likewise required —
        ``settings`` is ``Any``, and ``warn_return_any`` fails the typecheck gate
        on returning the raw value from an ``-> int`` function.
    """
    override = getattr(settings, "max_tokens", None)
    return int(override) if override else provider_default
