"""Shared prompt-string constants."""

from __future__ import annotations

# Prefilled into the next prompt after a background synthetic test exits non-zero,
# so the user can ask the CLI assistant for a quick RCA explanation. Both the core
# prompt builders and the shell reference it, so it lives in config (the lowest layer).
SUGGESTED_PROMPT_AFTER_FAILED_SYNTHETIC_TEST = "why did it fail?"
