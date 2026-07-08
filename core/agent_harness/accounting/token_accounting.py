"""Session-scoped token accounting and LLM run metadata for the agent harness."""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from core.llm.types import LLMResponse

_CHARS_PER_TOKEN = 4


@dataclass(frozen=True, slots=True)
class LlmRunInfo:
    """Best-effort metadata from one visible LLM response."""

    model: str | None = None
    provider: str | None = None
    latency_ms: int | None = None
    input_tokens: int | None = None
    output_tokens: int | None = None
    response_text: str | None = None
    final_system_prompt: str | None = None


def estimate_tokens(text: str) -> int:
    """Approximate token count from character length."""
    return len(text) // _CHARS_PER_TOKEN


def resolve_model_name(client: object) -> str | None:
    value = getattr(client, "_model", None)
    return value if isinstance(value, str) and value else None


def resolve_provider_name(client: object) -> str | None:
    provider_label = getattr(client, "_provider_label", None)
    if isinstance(provider_label, str) and provider_label:
        return provider_label.strip().lower().replace(" ", "_")
    name = type(client).__name__.lower()
    if "openai" in name:
        return "openai"
    if "bedrock" in name:
        return "bedrock"
    if "cli" in name:
        return "cli"
    if "anthropic" in name or "llmclient" in name:
        return "anthropic"
    return None


def record_llm_turn(
    session: Any,
    *,
    prompt: str,
    response: str,
    input_tokens: int | None = None,
    output_tokens: int | None = None,
) -> tuple[int, int, bool]:
    """Accumulate one LLM call on any session exposing ``tokens.record``."""
    if input_tokens is not None and output_tokens is not None:
        inp, out, estimated = input_tokens, output_tokens, False
    else:
        inp = estimate_tokens(prompt)
        out = estimate_tokens(response)
        estimated = True
    tokens = getattr(session, "tokens", None)
    if tokens is not None and callable(getattr(tokens, "record", None)):
        tokens.record(input_tokens=inp, output_tokens=out, estimated=estimated)
    return inp, out, estimated


def record_invoke_response(
    session: Any | None,
    *,
    prompt: str,
    response: LLMResponse,
) -> str:
    """Record an ``invoke()`` turn and return stripped response content."""
    content = response.content.strip()
    if session is not None:
        record_llm_turn(
            session,
            prompt=prompt,
            response=content,
            input_tokens=response.input_tokens,
            output_tokens=response.output_tokens,
        )
    return content


def build_llm_run_info(
    *,
    session: Any,
    prompt: str,
    response_text: str,
    started: float | None = None,
    client: object | None = None,
    model: str | None = None,
    provider: str | None = None,
    final_system_prompt: str | None = None,
) -> LlmRunInfo:
    """Record token usage and assemble metadata for prompt logging."""
    inp, out, _estimated = record_llm_turn(session, prompt=prompt, response=response_text)
    latency_ms = 0 if started is None else int((time.monotonic() - started) * 1000)
    return LlmRunInfo(
        model=model or (resolve_model_name(client) if client is not None else None),
        provider=provider or (resolve_provider_name(client) if client is not None else None),
        latency_ms=latency_ms,
        input_tokens=inp,
        output_tokens=out,
        response_text=response_text,
        final_system_prompt=final_system_prompt,
    )


def format_token_total(session: Any, *, direction: str) -> tuple[str, str]:
    """Return ``(row_label, formatted_value)`` for input or output tokens."""
    usage = session.tokens.totals
    measured = usage.get(f"{direction}_measured", 0)
    estimated = usage.get(f"{direction}_estimated", 0)
    total = usage.get(direction, 0)
    label = f"{direction} tokens"
    if estimated and measured:
        return (
            label,
            f"{total:,} ({measured:,} provider + {estimated:,} est.)",
        )
    if estimated:
        return (f"{label} (est.)", f"{total:,}")
    return (label, f"{total:,}")


__all__ = [
    "LlmRunInfo",
    "build_llm_run_info",
    "estimate_tokens",
    "format_token_total",
    "record_invoke_response",
    "record_llm_turn",
    "resolve_model_name",
    "resolve_provider_name",
]
