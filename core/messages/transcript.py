"""Transcript parsing helpers for provider message dictionaries."""

from __future__ import annotations

from typing import Any


def extract_last_assistant_text(messages: list[dict[str, Any]]) -> str:
    """Return the last non-empty assistant message as plain text.

    Walks the transcript in reverse and normalises string content and
    provider-specific content blocks (dict blocks, typed blocks, objects
    with ``type``/``text`` attributes).
    """
    for msg in reversed(messages):
        if msg.get("role") != "assistant":
            continue
        content = msg.get("content", "")
        if isinstance(content, str) and content.strip():
            return content.strip()
        if isinstance(content, list):
            parts: list[str] = []
            for block in content:
                if isinstance(block, str):
                    parts.append(block)
                    continue
                if isinstance(block, dict):
                    if block.get("type") == "text" and isinstance(block.get("text"), str):
                        parts.append(block["text"])
                    continue
                block_type = getattr(block, "type", None)
                block_text = getattr(block, "text", None)
                if block_type == "text" and isinstance(block_text, str):
                    parts.append(block_text)
            text = " ".join(p for p in parts if p).strip()
            if text:
                return text
    return ""


__all__ = ["extract_last_assistant_text"]
