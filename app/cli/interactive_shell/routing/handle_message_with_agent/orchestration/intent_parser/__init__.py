"""Structural text helpers shared by the literal command fast path and shell layer.

This package intentionally does NOT infer user intent from natural language.
Tool/action selection is owned entirely by the LLM action planner (see
``llm_action_planner``); the only logic kept here is:

- ``normalize_intent_text`` / ``is_single_edit_typo`` — typo-tolerant matching
  of *literal* command aliases for the deterministic command fast path.
- ``shell_action`` — a ``PlannedAction`` factory for the explicit ``!cmd``
  shell-passthrough fast path.
- ``IS_WINDOWS`` — platform flag consumed by the shell execution layer.
"""

from __future__ import annotations

import os

from .action_builders import shell_action
from .typo_normalization import (
    is_single_edit_typo,
    normalize_intent_text,
)

IS_WINDOWS = os.name == "nt"

__all__ = [
    "IS_WINDOWS",
    "is_single_edit_typo",
    "normalize_intent_text",
    "shell_action",
]
