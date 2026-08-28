"""Observe LLM usage during a foreground investigation for turn telemetry."""

from __future__ import annotations

import contextlib
from collections.abc import Iterator
from dataclasses import dataclass

from core.llm.shared.usage import set_usage_hook


@dataclass
class InvestigationLlmUsage:
    """Accumulated provider-reported LLM usage for one investigation run."""

    model: str = ""
    input_tokens: int = 0
    output_tokens: int = 0

    @property
    def observed(self) -> bool:
        return bool(self.model) or self.input_tokens > 0 or self.output_tokens > 0


@contextlib.contextmanager
def observe_investigation_llm_usage() -> Iterator[InvestigationLlmUsage]:
    """Accumulate provider-reported token usage while the body runs.

    Registration is best-effort: if another owner already holds the process-wide
    usage hook (`core.llm.shared.usage.set_usage_hook`), the investigation proceeds
    without usage observation rather than failing.
    """
    usage = InvestigationLlmUsage()

    def _hook(model: str, tokens_in: int, tokens_out: int) -> None:
        if model:
            usage.model = model
        usage.input_tokens += tokens_in
        usage.output_tokens += tokens_out

    registered = False
    with contextlib.suppress(RuntimeError):
        set_usage_hook(_hook)
        registered = True
    try:
        yield usage
    finally:
        if registered:
            set_usage_hook(None)


def resolve_configured_llm_identity() -> tuple[str, str]:
    """Best-effort ``(provider, model)`` from the configured LLM settings."""
    try:
        from config.llm_settings import resolve_llm_settings

        settings = resolve_llm_settings()
        provider = settings.provider
        # Settings attributes use the underscore form; the slug can be hyphenated.
        model = str(getattr(settings, f"{provider.replace('-', '_')}_reasoning_model", "") or "")
        return provider, model
    except Exception:
        return "", ""


__all__ = [
    "InvestigationLlmUsage",
    "observe_investigation_llm_usage",
    "resolve_configured_llm_identity",
]
