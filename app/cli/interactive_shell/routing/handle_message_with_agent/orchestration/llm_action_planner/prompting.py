"""Prompt composition and sanitization for planner LLM calls."""

from __future__ import annotations

import re

from .constants import _MAX_TEXT_LEN, _SYSTEM_PROMPT_BASE


def _system_prompt() -> str:
    return _SYSTEM_PROMPT_BASE


def _sanitise_text(text: str) -> str:
    sanitised = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]", "", text)
    sanitised = re.sub(r"<{3,}|>{3,}", " ", sanitised)
    return sanitised[:_MAX_TEXT_LEN]
