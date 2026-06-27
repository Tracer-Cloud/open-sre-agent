"""Context-window budgeting for shared agent tool loops."""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger(__name__)

# Prompt windows are substring-matched against provider model ids. Unknown
# models use a conservative default so we trim early rather than overflow.
_MODEL_CONTEXT_WINDOWS: dict[str, int] = {
    "claude": 200_000,
    "gpt-4o": 128_000,
    "gpt-4.1": 1_000_000,
    "gpt-4": 128_000,
    # gpt-5 window is conservatively pinned to 128k until confirmed for the
    # dated snapshot in use; raise once verified to reclaim headroom.
    "gpt-5": 128_000,
    "o1": 128_000,
    "o3": 128_000,
}
_DEFAULT_CONTEXT_WINDOW = 128_000

_RESPONSE_HEADROOM_TOKENS = 16_000
_TOKEN_BUDGET_CEILING = _DEFAULT_CONTEXT_WINDOW - _RESPONSE_HEADROOM_TOKENS

# Conservative char-to-token estimate for JSON-heavy tool payloads.
_TOKENS_PER_CHAR = 0.50

_TRUNCATION_MARKER = "…[truncated to fit context budget]"
_TRUNCATION_SAFETY_TOKENS = 2_000
_TRUNCATION_MIN_TOKENS = 1_000

_PINNED_MESSAGE_KEY = "_opensre_seed"
_DUPLICATE_RESULT_KEY = "_opensre_duplicate_result"


@dataclass(frozen=True)
class _ToolExchange:
    start: int
    end: int
    token_estimate: int
    duplicate_only: bool


def _is_pinned_message(message: dict[str, Any]) -> bool:
    """Whether whole-pair eviction must preserve this message."""
    return bool(message.get(_PINNED_MESSAGE_KEY))


def _is_duplicate_result_message(message: dict[str, Any]) -> bool:
    """Whether this message belongs to a duplicate-only tool exchange."""
    return bool(message.get(_DUPLICATE_RESULT_KEY))


def _has_tool_use_block(content: Any) -> bool:
    if not isinstance(content, list):
        return False
    return any(
        isinstance(block, dict) and (block.get("type") == "tool_use" or "toolUse" in block)
        for block in content
    )


def _candidate_exchange(
    messages: list[dict[str, Any]],
    *,
    start: int,
    end: int,
) -> _ToolExchange | None:
    exchange_messages = messages[start:end]
    if any(_is_pinned_message(message) for message in exchange_messages):
        return None

    result_messages = exchange_messages[1:]
    duplicate_only = bool(result_messages) and all(
        _is_duplicate_result_message(message) for message in result_messages
    )
    return _ToolExchange(
        start=start,
        end=end,
        token_estimate=estimate_message_tokens(exchange_messages),
        duplicate_only=duplicate_only,
    )


def _append_candidate(
    candidates: list[_ToolExchange],
    messages: list[dict[str, Any]],
    *,
    start: int,
    end: int,
) -> None:
    candidate = _candidate_exchange(messages, start=start, end=end)
    if candidate is not None:
        candidates.append(candidate)


def _tool_exchange_candidates(messages: list[dict[str, Any]]) -> list[_ToolExchange]:
    candidates: list[_ToolExchange] = []
    for index, message in enumerate(messages):
        if message.get("role") != "assistant":
            continue

        if _has_tool_use_block(message.get("content")):
            _append_candidate(candidates, messages, start=index, end=min(index + 2, len(messages)))
            continue

        tool_calls = message.get("tool_calls")
        if tool_calls and isinstance(tool_calls, list):
            call_ids = {tc.get("id") for tc in tool_calls if isinstance(tc, dict) and tc.get("id")}
            end = index + 1
            while end < len(messages):
                follower = messages[end]
                if follower.get("role") == "tool" and follower.get("tool_call_id") in call_ids:
                    end += 1
                else:
                    break
            _append_candidate(candidates, messages, start=index, end=end)
    return candidates


def _eviction_priority(exchange: _ToolExchange) -> tuple[int, int, int]:
    """Lower priority tuple is evicted first."""
    duplicate_rank = 0 if exchange.duplicate_only else 1
    return (duplicate_rank, -exchange.token_estimate, exchange.start)


def context_budget_ceiling_for_model(model: str | None) -> int:
    """Trim ceiling for the active model = its context window − response headroom.

    Substring match (case-insensitive) so dated snapshots and provider prefixes
    resolve to the right family. Unknown → conservative default, which only ever
    trims slightly early; it never risks an overflow.
    """
    window = _DEFAULT_CONTEXT_WINDOW
    if model:
        key = model.lower()
        for family, family_window in _MODEL_CONTEXT_WINDOWS.items():
            if family in key:
                window = family_window
                break
    return max(window - _RESPONSE_HEADROOM_TOKENS, _RESPONSE_HEADROOM_TOKENS)


def estimate_message_tokens(
    messages: list[dict[str, Any]],
    *,
    system: str | None = None,
    tools: list[dict[str, Any]] | None = None,
) -> int:
    """Cheap upper-bound token estimate covering everything Anthropic sees.

    Anthropic counts ``messages`` + ``system`` + ``tools`` toward the 200k
    prompt limit. Earlier versions counted only ``messages`` and trimmed
    aggressively while system + tools (tens of thousands of tokens for
    opensre's 100+ tool registry) silently pushed us over the line.
    """
    total = 0
    for message in messages:
        content = message.get("content", "")
        if isinstance(content, str):
            total += int(len(content) * _TOKENS_PER_CHAR)
        elif isinstance(content, list):
            for block in content:
                if isinstance(block, dict):
                    total += int(len(json.dumps(block, default=str)) * _TOKENS_PER_CHAR)
                elif isinstance(block, str):
                    total += int(len(block) * _TOKENS_PER_CHAR)
    if system:
        total += int(len(system) * _TOKENS_PER_CHAR)
    if tools:
        for schema in tools:
            total += int(len(json.dumps(schema, default=str)) * _TOKENS_PER_CHAR)
    return total


def trim_lowest_value_tool_pair(messages: list[dict[str, Any]]) -> bool:
    """Drop one non-pinned tool exchange using the eviction heuristic."""
    candidates = _tool_exchange_candidates(messages)
    if not candidates:
        return False

    selected = min(candidates, key=_eviction_priority)
    del messages[selected.start : selected.end]
    return True


def _shrink_text(text: str, max_chars: int) -> tuple[str, bool]:
    """Truncate ``text`` to ``max_chars`` (inclusive of the marker). No-op if it fits."""
    if len(text) <= max_chars:
        return text, False
    keep = max(max_chars - len(_TRUNCATION_MARKER), 0)
    return text[:keep] + _TRUNCATION_MARKER, True


def _sum_text_chars(node: Any) -> int:
    """Total char length of every truncatable string in a content tree.

    Targets the bulky payload fields opensre actually emits: a dict's ``content``
    / ``text`` (Anthropic tool_result + text blocks) and bare strings inside
    lists, recursing through nested dicts/lists.
    """
    total = 0
    if isinstance(node, dict):
        for key, value in node.items():
            if isinstance(value, str) and key in ("content", "text"):
                total += len(value)
            elif isinstance(value, (list, dict)):
                total += _sum_text_chars(value)
    elif isinstance(node, list):
        for value in node:
            if isinstance(value, str):
                total += len(value)
            elif isinstance(value, (list, dict)):
                total += _sum_text_chars(value)
    return total


def _apply_text_factor(node: Any, factor: float) -> bool:
    """Shrink every truncatable string in a content tree to ~``factor`` of its
    length, mutating in place. Returns whether anything changed."""
    changed = False
    if isinstance(node, dict):
        for key, value in node.items():
            if isinstance(value, str) and key in ("content", "text"):
                new_value, slot_changed = _shrink_text(value, max(int(len(value) * factor), 0))
                if slot_changed:
                    node[key] = new_value
                    changed = True
            elif isinstance(value, (list, dict)):
                changed = _apply_text_factor(value, factor) or changed
    elif isinstance(node, list):
        for idx, value in enumerate(node):
            if isinstance(value, str):
                new_value, slot_changed = _shrink_text(value, max(int(len(value) * factor), 0))
                if slot_changed:
                    node[idx] = new_value
                    changed = True
            elif isinstance(value, (list, dict)):
                changed = _apply_text_factor(value, factor) or changed
    return changed


def truncate_content(content: Any, max_chars: int) -> tuple[Any, bool]:
    """Shrink a message's ``content`` so its char length is ~``max_chars``.

    String content is cut directly. List content (Anthropic block lists) is
    truncated proportionally across its text slots so the whole message lands
    near the budget rather than zeroing the first slot. Returns the (possibly
    same, mutated-in-place) content object and whether anything changed.
    """
    if isinstance(content, str):
        return _shrink_text(content, max_chars)
    if isinstance(content, list):
        total = _sum_text_chars(content)
        if total <= max_chars:
            return content, False
        factor = max_chars / total if total else 0.0
        return content, _apply_text_factor(content, factor)
    return content, False


def _truncate_largest_message(
    messages: list[dict[str, Any]],
    *,
    system: str | None,
    tools: list[dict[str, Any]] | None,
    ceiling: int,
) -> bool:
    """Truncate the biggest still-shrinkable message so the prompt fits.

    Tries messages largest-first (so an untruncatable assistant ``tool_calls``
    turn doesn't block a truncatable tool-result behind it) and stops at the
    first one that actually shrinks. Each successful call strictly reduces the
    total, guaranteeing the caller's loop terminates. Returns False when no
    message can be shrunk further — the caller then lets the API surface the
    error rather than spinning.
    """
    order = sorted(
        range(len(messages)),
        key=lambda i: estimate_message_tokens([messages[i]]),
        reverse=True,
    )
    for idx in order:
        overhead = estimate_message_tokens(
            [m for i, m in enumerate(messages) if i != idx], system=system, tools=tools
        )
        budget_tokens = max(ceiling - overhead - _TRUNCATION_SAFETY_TOKENS, _TRUNCATION_MIN_TOKENS)
        max_chars = int(budget_tokens / _TOKENS_PER_CHAR)
        new_content, changed = truncate_content(messages[idx].get("content"), max_chars)
        if changed:
            messages[idx]["content"] = new_content
            return True
    return False


def enforce_context_budget(
    messages: list[dict[str, Any]],
    *,
    system: str | None = None,
    tools: list[dict[str, Any]] | None = None,
    ceiling: int = _TOKEN_BUDGET_CEILING,
) -> None:
    """Trim low-value tool exchanges until the prompt fits under ``ceiling``."""
    while estimate_message_tokens(messages, system=system, tools=tools) > ceiling:
        if not trim_lowest_value_tool_pair(messages):
            if not _truncate_largest_message(messages, system=system, tools=tools, ceiling=ceiling):
                logger.warning(
                    "[agent] context still over budget after trimming + truncation "
                    "(ceiling=%d); letting the request proceed",
                    ceiling,
                )
                return
            logger.warning(
                "[agent] truncated oversized message to fit context budget (ceiling=%d)", ceiling
            )
            continue
        logger.warning(
            "[agent] trimmed low-value tool pair to fit context budget (ceiling=%d)", ceiling
        )
