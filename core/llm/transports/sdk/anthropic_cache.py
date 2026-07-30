"""Anthropic prompt-cache breakpoint markers.

A leaf module: both the agent client and the plain messages client mark cache
breakpoints, and neither imports the other. Anthropic caches the request prefix
up to each ``cache_control`` marker, so a byte-stable prefix plus a marker turns
resent input into cache reads (~0.1x input price) instead of full-price tokens.

Marking a prefix shorter than the model's minimum cacheable length is safe: the
request succeeds and simply is not cached, so callers do not need to measure
prompt size before marking.

Not every deployment supports the markers themselves: Bedrock rejects
``cache_control`` with a 400 for Claude models that predate prompt caching.
Clients therefore treat caching as best-effort — on a 400 that blames the
markers (:func:`is_cache_unsupported_error`), they retry once without markers
(:func:`strip_cache_markers`) and stop marking for the client's lifetime, so an
unsupported model works uncached instead of failing every call.
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


#: Substrings a provider 400 uses when the request failed *because of* the
#: cache markers: Anthropic names the offending ``cache_control`` field,
#: Bedrock's Converse-era wording says "cachePoint" / "prompt caching".
_CACHE_UNSUPPORTED_SIGNALS: Final[tuple[str, ...]] = (
    "cache_control",
    "cachepoint",
    "prompt caching",
    "prompt cache",
    # Bedrock's ValidationException often rejects the request without naming the
    # offending field, so the generic wording has to count as a signal too. These
    # are matched only on a 400, where the request is already failing.
    "does not support the requested feature",
    "invalid request body",
)


def is_cache_unsupported_error(err: Exception) -> bool:
    """True when a bad-request error blames the cache markers themselves.

    Only meaningful for 400-class errors — callers must check the error type
    first so unrelated failures (auth, rate limits) are never eaten.
    """
    message = str(err).lower()
    return any(signal in message for signal in _CACHE_UNSUPPORTED_SIGNALS)


def strip_cache_markers(kwargs: dict[str, Any]) -> dict[str, Any]:
    """Copy of request ``kwargs`` with every marker this module adds removed.

    Strips exactly the spots :func:`cached_system`, :func:`tools_with_cache`,
    and :func:`messages_with_cache` write to — system blocks, top-level tool
    schemas, and message content blocks. It deliberately does not recurse into
    tool ``input_schema`` bodies, where ``cache_control`` could be a
    legitimate user-defined property name.
    """
    out = dict(kwargs)
    system = out.get("system")
    if isinstance(system, list):
        blocks = [_block_without_marker(block) for block in system]
        # Exactly invert cached_system: a lone text block goes back to the plain
        # string a non-caching client would have sent, since a model that rejects
        # the markers may not accept block-form system either.
        if (
            len(blocks) == 1
            and isinstance(blocks[0], dict)
            and set(blocks[0]) == {"type", "text"}
            and blocks[0].get("type") == "text"
        ):
            out["system"] = blocks[0]["text"]
        else:
            out["system"] = blocks
    tools = out.get("tools")
    if isinstance(tools, list):
        out["tools"] = [_block_without_marker(tool) for tool in tools]
    messages = out.get("messages")
    if isinstance(messages, list):
        out["messages"] = [_message_without_marker(message) for message in messages]
    return out


def _block_without_marker(block: Any) -> Any:
    if isinstance(block, dict) and "cache_control" in block:
        return {key: value for key, value in block.items() if key != "cache_control"}
    return block


def _message_without_marker(message: Any) -> Any:
    if not isinstance(message, dict):
        return message
    content = message.get("content")
    if not isinstance(content, list):
        return message
    return {**message, "content": [_block_without_marker(block) for block in content]}


__all__ = [
    "ANTHROPIC_CACHE_CONTROL",
    "cached_system",
    "is_cache_unsupported_error",
    "messages_with_cache",
    "strip_cache_markers",
    "tools_with_cache",
]
