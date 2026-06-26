"""Shared constants for LLM action planning."""

from __future__ import annotations

__all__ = (
    "_MAX_TEXT_LEN",
    "_USER_TEMPLATE",
    "_OPENAI_STYLE_PROVIDERS",
)

_MAX_TEXT_LEN = 512
_USER_TEMPLATE = "USER MESSAGE (literal): <<<{text}>>>"

_OPENAI_STYLE_PROVIDERS = frozenset(
    {"openai", "openrouter", "gemini", "nvidia", "minimax", "ollama"}
)
