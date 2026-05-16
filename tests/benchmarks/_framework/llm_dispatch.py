from __future__ import annotations

import os
from collections.abc import Iterator
from contextlib import contextmanager

MODEL_ALIASES: dict[str, tuple[str, str]] = {
    "claude-4-sonnet": ("anthropic", "claude-sonnet-4-20250514"),
    "deepseek-v3.2": ("openrouter", "deepseek/deepseek-v3.2"),
    "gpt-5": ("openai", "gpt-5"),
    "gpt-4o": ("openai", "gpt-4o"),
}


def resolve_llm_alias(llm: str) -> tuple[str, str]:
    if ":" in llm:
        provider, model = llm.split(":", 1)
        return provider.strip(), model.strip()
    return MODEL_ALIASES.get(llm.lower(), ("", llm))


@contextmanager
def llm_environment(llm: str) -> Iterator[None]:
    """Temporarily pin OpenSRE's provider/model environment for one run."""

    provider, model = resolve_llm_alias(llm)
    updates: dict[str, str] = {"OPENSRE_BENCH_LLM": llm}
    if provider:
        updates["LLM_PROVIDER"] = provider
        prefix = provider.upper()
        updates[f"{prefix}_REASONING_MODEL"] = model
        updates[f"{prefix}_CLASSIFICATION_MODEL"] = model
        updates[f"{prefix}_TOOLCALL_MODEL"] = model

    previous = {key: os.environ.get(key) for key in updates}
    os.environ.update(updates)
    try:
        yield
    finally:
        for key, value in previous.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
