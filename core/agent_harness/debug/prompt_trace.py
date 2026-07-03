"""Persist assembled system prompts to the session JSONL for debugging."""

from __future__ import annotations

from typing import Any


def persist_turn_system_prompt(
    session: Any,
    *,
    phase: str,
    system_prompt: str,
) -> None:
    """Append the system prompt the LLM saw for one agent phase.

    Writes a ``message`` entry with ``role=system`` and metadata
    ``debug=system_prompt`` so ``/trace`` and session JSONL readers can find
    what influenced the model without re-deriving prompts by hand.
    """
    text = system_prompt.strip()
    if not text:
        return

    storage = getattr(session, "storage", None)
    session_id = getattr(session, "session_id", "")
    append_message = getattr(storage, "append_message", None)
    if not callable(append_message) or not isinstance(session_id, str) or not session_id:
        return

    append_message(
        session_id,
        role="system",
        content=text,
        metadata={"kind": phase, "debug": "system_prompt"},
    )


__all__ = ["persist_turn_system_prompt"]
