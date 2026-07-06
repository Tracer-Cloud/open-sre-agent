"""Process-wide LLM usage hook for token and cost accounting."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from core.llm.types import LLMResponse

UsageHook = Callable[[str, int, int], object]
_usage_hook: UsageHook | None = None


def set_usage_hook(hook: UsageHook | None) -> None:
    """Register or clear the process-wide usage observer (``model, tokens_in, tokens_out``)."""
    global _usage_hook
    if hook is not None and _usage_hook is not None:
        raise RuntimeError(
            "A usage hook is already registered. Either the previous owner "
            "failed to clear it (call set_usage_hook(None) in a finally), or "
            "two concurrent users of llm_client are conflicting. See the "
            "set_usage_hook docstring for the contract."
        )
    _usage_hook = hook


def emit_usage(model: str, tokens_in: int | None, tokens_out: int | None) -> None:
    """Notify the registered hook (if any). No-op when no hook or token counts missing."""
    hook = _usage_hook
    if hook is None:
        return
    if tokens_in is None and tokens_out is None:
        return
    hook(model, int(tokens_in or 0), int(tokens_out or 0))


def coerce_usage_tokens(
    usage: Any,
    *,
    input_key: str,
    output_key: str,
) -> tuple[int | None, int | None]:
    if usage is None:
        return None, None
    if isinstance(usage, dict):
        raw_in = usage.get(input_key)
        raw_out = usage.get(output_key)
    else:
        raw_in = getattr(usage, input_key, None)
        raw_out = getattr(usage, output_key, None)
    inp = int(raw_in) if isinstance(raw_in, (int, float)) else None
    out = int(raw_out) if isinstance(raw_out, (int, float)) else None
    return inp, out


def emit_provider_usage(
    model: str,
    usage: Any,
    *,
    input_key: str,
    output_key: str,
) -> None:
    """Emit provider-reported usage from an arbitrary usage payload (agent clients)."""
    inp, out = coerce_usage_tokens(usage, input_key=input_key, output_key=output_key)
    emit_usage(model, inp, out)


def llm_response_with_usage(
    content: str,
    model: str,
    usage: Any,
    *,
    input_key: str,
    output_key: str,
) -> LLMResponse:
    inp, out = coerce_usage_tokens(usage, input_key=input_key, output_key=output_key)
    emit_usage(model, inp, out)
    return LLMResponse(content=content, input_tokens=inp, output_tokens=out)
