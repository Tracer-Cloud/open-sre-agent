"""Anthropic prompt-cache breakpoint markers.

A leaf module: both the agent client and the plain messages client mark cache
breakpoints, and neither imports the other. Anthropic caches the request prefix
up to each ``cache_control`` marker, so a byte-stable prefix plus a marker turns
resent input into cache reads (~0.1x input price) instead of full-price tokens.

Marking a prefix shorter than the model's minimum cacheable length is safe: the
request succeeds and simply is not cached, so callers do not need to measure
prompt size before marking.
"""

from __future__ import annotations

from typing import Any, Final

#: Anthropic's 5-minute ephemeral cache. Written once, read by later requests
#: whose prefix matches byte-for-byte.
ANTHROPIC_CACHE_CONTROL: Final[dict[str, str]] = {"type": "ephemeral"}


def cached_system(system: str) -> list[dict[str, Any]]:
    """Return ``system`` as a single text block carrying a cache breakpoint."""
    return [
        {
            "type": "text",
            "text": system,
            "cache_control": dict(ANTHROPIC_CACHE_CONTROL),
        }
    ]


def tools_with_cache(tools: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Mark the last tool schema, caching the whole tool block before it."""
    if not tools:
        return tools
    cached = [dict(tool) for tool in tools]
    cached[-1] = {**cached[-1], "cache_control": dict(ANTHROPIC_CACHE_CONTROL)}
    return cached


def messages_with_cache(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Mark the newest message's last content block as a cache breakpoint.

    Tools and system cover the static prefix; this marker lets the growing
    conversation history accrue incremental cache hits across ReAct
    iterations. Copies, never mutates — the caller reuses ``messages`` on
    retries. Content that cannot carry a marker (empty text, unknown shapes)
    is left untouched.
    """
    if not messages:
        return messages
    last = messages[-1]
    content = last.get("content")
    if isinstance(content, str) and content:
        marked_content: list[Any] = [
            {
                "type": "text",
                "text": content,
                "cache_control": dict(ANTHROPIC_CACHE_CONTROL),
            }
        ]
    elif isinstance(content, list) and content and isinstance(content[-1], dict):
        # Copy every block, not just the marked one: the transcript is reused
        # across ReAct iterations, and a shared dict would let a later payload
        # mutation write through into live history.
        marked_content = [
            dict(block) if isinstance(block, dict) else block for block in content[:-1]
        ]
        marked_content.append({**content[-1], "cache_control": dict(ANTHROPIC_CACHE_CONTROL)})
    else:
        return messages
    return [*messages[:-1], {**last, "content": marked_content}]


__all__ = [
    "ANTHROPIC_CACHE_CONTROL",
    "cached_system",
    "messages_with_cache",
    "tools_with_cache",
]
