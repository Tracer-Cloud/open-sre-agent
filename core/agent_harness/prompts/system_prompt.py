"""Shared OpenSRE system prompt loaded from ``opensre_system_prompt.md``.

Action and assistant both use this file as their stable system base. The
markdown lives next to the action assembler; this leaf is the one import
path so the peer agent packages stay acyclic.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

_PROMPT_FILENAME = "opensre_system_prompt.md"
_PROMPT_PATH = Path(__file__).parent / "action" / _PROMPT_FILENAME


@lru_cache(maxsize=1)
def load_opensre_system_prompt() -> str:
    """Return the bundled system prompt markdown."""
    return _PROMPT_PATH.read_text(encoding="utf-8")


OPENSRE_SYSTEM_PROMPT = load_opensre_system_prompt()

__all__ = (
    "OPENSRE_SYSTEM_PROMPT",
    "_PROMPT_FILENAME",
    "_PROMPT_PATH",
    "load_opensre_system_prompt",
)
