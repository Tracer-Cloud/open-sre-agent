"""Structural text helpers shared by literal command detection and shell parsing.

This package intentionally does NOT infer user intent from natural language.
Tool/action selection is owned entirely by the shell action agent; the only logic
kept here is:

- ``normalize_intent_text`` / ``is_single_edit_typo`` — typo-tolerant matching
  of *literal* command aliases for deterministic command-text detection.
- ``IS_WINDOWS`` — platform flag consumed by the shell execution layer.
"""

from __future__ import annotations

import os

from .typo_normalization import (
    is_single_edit_typo,
    normalize_intent_text,
)

IS_WINDOWS = os.name == "nt"

__all__ = [
    "IS_WINDOWS",
    "is_single_edit_typo",
    "normalize_intent_text",
]
