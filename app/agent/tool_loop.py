"""Shared tool-calling ReAct primitives.

This module owns the provider-agnostic machinery for running a "think → call
tools → observe" loop against the registered tool set: parallel tool execution,
provider-specific assistant/tool-result message shaping, and the context-window
budget enforcement that keeps long loops under each model's prompt limit.

Two consumers build on top of it:

* :mod:`app.agent.investigation` — the investigation agent layers evidence
  collection, seed calls, and diagnosis parsing on top of these helpers and its
  own loop orchestration.
* the interactive shell's tool-gathering pass — uses :func:`run_tool_calling_loop`
  to let the REPL assistant pull live data from the *same* registered tools the
  investigation uses before composing a conversational answer.

Keeping the loop primitives here (rather than private to ``investigation.py``)
means both surfaces share one implementation of the subtle, well-tested context
budgeting and provider message shaping.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Callable
from concurrent.futures import Future, ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from typing import Any

from app.services.agent_llm_client import ToolCall
from app.tools.registered_tool import RegisteredTool
from app.utils.tool_trace import redact_sensitive

logger = logging.getLogger(__name__)

_TOOL_EXECUTOR_WORKERS = 10
_UNSET: object = object()  # sentinel distinguishing "not yet started" from a None tool result

# Defensive context-window ceiling. Below this we never trim; above this we
# drop the oldest tool_use/tool_result pair until back under the ceiling.
#
# CRITICAL: the ceiling MUST be derived from the ACTIVE model's context window,
# not hardcoded. A previous flat 170k ceiling was tuned for Anthropic's 200k
# window and silently overflowed every OpenAI run — gpt-4o's window is 128k, so
# trimming "down to 170k" still exceeds the API limit and the call is rejected
# with context_length_exceeded (observed on 40-service train-ticket cases where
# tool payloads are large). Always size the ceiling per-model.
#
# Per-model prompt windows (tokens). Substring-matched against the model id, so
# dated snapshots (gpt-4o-2024-11-20) and Bedrock prefixes (us.anthropic.claude)
# resolve correctly. Unknown models fall back to the conservative default — it
# is always safe to assume a SMALLER window (we trim a little early) and never
# safe to assume a larger one (we overflow and the call dies).
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

# Reserve for the model's response + estimator slack. ceiling = window - this.
_RESPONSE_HEADROOM_TOKENS = 16_000

# Default ceiling when the active model is unknown at the call site (also the
# value used by callers/tests that don't pass an explicit ceiling).
_TOKEN_BUDGET_CEILING = _DEFAULT_CONTEXT_WINDOW - _RESPONSE_HEADROOM_TOKENS

# ratio=0.5 over-estimates slightly to absorb JSON-structural overhead in tool
# payloads — better to trim one pair early than to under-count and overflow.
# Overflow logs showed real tokens/char of 0.4–0.5 for opensre's tool-result
# mix, so 0.5 is the safe upper edge.
_TOKENS_PER_CHAR = 0.50

# Last-resort truncation. Whole-pair trimming (``_trim_oldest_tool_pair``) drops
# tool exchanges oldest-first, but once every tool pair is gone the base prompt
# can still exceed the window — e.g. a 40-service train-ticket alert whose initial
# user message is huge, or any single non-tool message that isn't part of a
# trimmable pair. The old code returned there and let the request overflow. When
# trimming is exhausted but the prompt is still over budget, we truncate the
# largest message's text payload in place so the request can never exceed the
# model window. Marker tells the model (and anyone reading the trace) that
# content was elided.
_TRUNCATION_MARKER = "…[truncated to fit context budget]"
# Slack subtracted from the per-message budget so the post-truncation estimate
# lands safely under the ceiling rather than exactly on it.
_TRUNCATION_SAFETY_TOKENS = 2_000
# Floor for a single message's content budget. If system+tools+other messages
# already consume the whole ceiling, we still leave at least this much so the
# truncated message carries some signal instead of being blanked.
_TRUNCATION_MIN_TOKENS = 1_000


# Callback type: called with (event_kind, data_dict) during the agent loop.
# event_kind values: "tool_start", "tool_end", "llm_start", "agent_start", "agent_end"
AgentEventCallback = Callable[[str, dict[str, Any]], None]


def _context_budget_ceiling_for_model(model: str | None) -> int:
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


def _estimate_message_tokens(
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


def _trim_oldest_tool_pair(messages: list[dict[str, Any]]) -> bool:
    """Drop the oldest tool-call exchange (assistant + paired results).

    Provider message shapes differ:

      * **Anthropic / Bedrock**: the assistant message's ``content`` is a list
        of blocks; tool calls show up as blocks with ``type == "tool_use"``.
        Tool results come in the SINGLE next user message as ``tool_result``
        blocks. So the pair is ``[assistant, user]`` — always two messages.

      * **OpenAI**: the assistant message has a top-level ``tool_calls`` field
        (``content`` is a plain string or empty). Each tool call produces a
        SEPARATE follow-up message with ``role == "tool"`` and
        ``tool_call_id`` matching the assistant's call id. So the exchange is
        ``[assistant, tool, tool, ...]`` — variable length.

    Returning False when an OpenAI exchange wasn't detected was the bug that
    let gpt-4o cells overflow at 181k tokens during the 2026-06-05 floorsweep:
    the Anthropic-only check skipped every OpenAI assistant turn (whose
    ``content`` is a string), so the trimmer found nothing to drop, returned
    False, and the runtime ceiling never fired before the API call.

    Returns True if an exchange was dropped, False when nothing trimmable
    remains (e.g. only the initial user prompt + a no-tool-call assistant
    turn is left).
    """
    for index, message in enumerate(messages):
        if message.get("role") != "assistant":
            continue

        # Anthropic shape: tool_use blocks inside content list.
        content = message.get("content")
        if isinstance(content, list):
            has_tool_use = any(
                isinstance(block, dict) and block.get("type") == "tool_use" for block in content
            )
            if has_tool_use:
                # Drop the assistant turn + the paired user turn carrying the
                # tool_result blocks. If the user turn is missing (truncated
                # mid-iteration), ``del [i:i+2]`` safely drops just the
                # assistant turn.
                del messages[index : index + 2]
                return True

        # OpenAI shape: tool_calls as a top-level field. Drop the assistant
        # message + all immediately-following role:"tool" messages whose
        # tool_call_id matches one of the assistant's tool_calls (per OpenAI's
        # Chat Completions contract).
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
            del messages[index:end]
            return True
    return False


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


def _truncate_content(content: Any, max_chars: int) -> tuple[Any, bool]:
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
        key=lambda i: _estimate_message_tokens([messages[i]]),
        reverse=True,
    )
    for idx in order:
        overhead = _estimate_message_tokens(
            [m for i, m in enumerate(messages) if i != idx], system=system, tools=tools
        )
        budget_tokens = max(ceiling - overhead - _TRUNCATION_SAFETY_TOKENS, _TRUNCATION_MIN_TOKENS)
        max_chars = int(budget_tokens / _TOKENS_PER_CHAR)
        new_content, changed = _truncate_content(messages[idx].get("content"), max_chars)
        if changed:
            messages[idx]["content"] = new_content
            return True
    return False


def _enforce_context_budget(
    messages: list[dict[str, Any]],
    *,
    system: str | None = None,
    tools: list[dict[str, Any]] | None = None,
    ceiling: int = _TOKEN_BUDGET_CEILING,
) -> None:
    """Trim oldest tool pairs until prompt fits under ``ceiling``.

    ``ceiling`` MUST be sized for the active model (see
    ``_context_budget_ceiling_for_model``); the default is the conservative
    unknown-model value. No-op on the happy path: the estimate covers messages
    + system + tools in one pass and returns under the ceiling for normal
    investigations. Only fires on long investigations where unbounded tool
    history has pushed the prompt past the model's limit.
    """
    while _estimate_message_tokens(messages, system=system, tools=tools) > ceiling:
        if not _trim_oldest_tool_pair(messages):
            # Whole-pair trimming exhausted but still over budget: the remaining
            # base prompt (e.g. an oversized initial alert or other non-tool
            # message) is itself too large. Truncate its payload so the request
            # can't overflow. If nothing is left to shrink, return and let the
            # API surface the error rather than spin.
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
            "[agent] trimmed oldest tool pair to fit context budget (ceiling=%d)", ceiling
        )


def _build_synthetic_assistant_tool_call_msg(
    llm: Any,
    tool_calls: list[ToolCall],
) -> dict[str, Any]:
    """Build an assistant message that looks like the LLM requested these tool calls.

    This lets us inject pre-seeded tool results into the conversation in a format
    the LLM client already understands, without adding special-case handling.
    """
    from app.services.agent_llm_client import (
        AnthropicAgentClient,
        BedrockConverseAgentClient,
        CLIBackedAgentClient,
        OpenAIAgentClient,
    )

    if isinstance(llm, BedrockConverseAgentClient):
        from app.services.bedrock_converse import build_assistant_tool_use_message

        return build_assistant_tool_use_message(tool_calls)

    if isinstance(llm, AnthropicAgentClient):
        content = [
            {
                "type": "tool_use",
                "id": tc.id,
                "name": tc.name,
                "input": tc.input,
            }
            for tc in tool_calls
        ]
        return {"role": "assistant", "content": content}

    if isinstance(llm, OpenAIAgentClient):
        return {
            "role": "assistant",
            "content": None,
            "tool_calls": [
                {
                    "id": tc.id,
                    "type": "function",
                    "function": {"name": tc.name, "arguments": json.dumps(tc.input)},
                }
                for tc in tool_calls
            ],
        }

    if isinstance(llm, CLIBackedAgentClient):
        return llm.build_assistant_message("", tool_calls)

    # Fallback: plain text summary
    names = ", ".join(tc.name for tc in tool_calls)
    return {"role": "assistant", "content": f"I will start by querying: {names}"}


def _run_parallel(
    tool_calls: list[ToolCall],
    tools: list[RegisteredTool],
    resolved_integrations: dict[str, Any],
) -> list[Any]:
    tool_map = {t.name: t for t in tools}

    def _call(tc: ToolCall) -> Any:
        tool = tool_map.get(tc.name)
        if tool is None:
            return {"error": f"unknown tool: {tc.name}"}
        try:
            validation_error = tool.validate_public_input(tc.input)
            if validation_error:
                return {"error": validation_error}
            injected = tool.extract_params(resolved_integrations)
            kwargs = {**injected, **tc.input}
            return tool.run(**kwargs)
        except Exception as exc:
            logger.warning("[tool:%s] failed: %s", tc.name, exc)
            return {"error": str(exc)}

    if len(tool_calls) == 1:
        return [_call(tool_calls[0])]

    results: list[Any] = [_UNSET] * len(tool_calls)
    submitted: dict[
        Future[Any], int
    ] = {}  # future -> index, built incrementally to survive partial submit
    try:
        with ThreadPoolExecutor(max_workers=min(_TOOL_EXECUTOR_WORKERS, len(tool_calls))) as pool:
            for i, tc in enumerate(tool_calls):
                submitted[pool.submit(_call, tc)] = i
            for fut in as_completed(submitted):
                try:
                    results[submitted[fut]] = fut.result()
                except Exception as fut_exc:  # noqa: BLE001  # lgtm[py/catch-base-exception]
                    results[submitted[fut]] = {"error": str(fut_exc)}
    except RuntimeError as exc:
        # interpreter is shutting down; executor.__exit__ has already waited for submitted futures
        logger.warning("[_run_parallel] RuntimeError – falling back to sequential: %s", exc)
        for fut, i in submitted.items():
            if results[i] is _UNSET and fut.done():
                try:
                    results[i] = fut.result()
                except Exception as fut_exc:  # noqa: BLE001  # lgtm[py/catch-base-exception]
                    results[i] = {"error": str(fut_exc)}
        for i, tc in enumerate(tool_calls):
            if results[i] is _UNSET:
                results[i] = _call(tc)
    return results


def _public_tool_input(value: dict[str, Any]) -> dict[str, Any]:
    redacted = redact_sensitive(value)
    return {
        key: item
        for key, item in redacted.items()
        if item != "[runtime object]" and item != "[redacted]"
    }


def _tool_source(tools: list[RegisteredTool], tool_name: str) -> str:
    for tool in tools:
        if tool.name == tool_name:
            return str(tool.source)
    return "unknown"


def _summarise(output: Any) -> str:
    if isinstance(output, dict) and "error" in output:
        return f"error: {output['error']}"
    text = json.dumps(output, default=str)
    return text[:120] + "…" if len(text) > 120 else text


def _build_assistant_msg(llm: Any, response: Any) -> dict[str, Any]:
    from app.services.agent_llm_client import AnthropicAgentClient, BedrockConverseAgentClient

    if isinstance(llm, (AnthropicAgentClient, BedrockConverseAgentClient)):
        return llm.build_assistant_message(response.raw_content)
    # Use raw_content when set — preserves provider-specific fields such as
    # Gemini's thought_signature that must be echoed back in the next request.
    if response.raw_content is not None:
        return response.raw_content  # type: ignore[no-any-return]
    result: dict[str, Any] = llm.build_assistant_message(response.content, response.tool_calls)
    return result


def _build_tool_result_messages(
    llm: Any,
    tool_calls: list[ToolCall],
    results: list[Any],
) -> list[dict[str, Any]]:
    from app.services.agent_llm_client import AnthropicAgentClient, OpenAIAgentClient

    if isinstance(llm, AnthropicAgentClient):
        return [llm.build_tool_result_message(tool_calls, results)]
    if isinstance(llm, OpenAIAgentClient):
        return llm.build_tool_result_messages(tool_calls, results)
    return [llm.build_tool_result_message(tool_calls, results)]


@dataclass
class ToolLoopResult:
    """Outcome of :func:`run_tool_calling_loop`.

    ``messages`` is the full conversation (mutated in place and returned for
    convenience), ``final_text`` is the assistant's last no-tool-call turn (the
    conversational answer, empty when the loop hit the iteration cap), and
    ``executed`` is the ordered list of ``(tool_call, output)`` pairs run during
    the loop.
    """

    messages: list[dict[str, Any]]
    final_text: str
    executed: list[tuple[ToolCall, Any]] = field(default_factory=list)
    hit_iteration_cap: bool = False


def run_tool_calling_loop(
    *,
    llm: Any,
    system: str,
    messages: list[dict[str, Any]],
    tools: list[RegisteredTool],
    resolved_integrations: dict[str, Any],
    max_iterations: int,
    on_event: AgentEventCallback | None = None,
) -> ToolLoopResult:
    """Run a generic think → call-tools → observe loop and return its outcome.

    Unlike :class:`app.agent.investigation.ConnectedInvestigationAgent`, this is
    a plain conversational loop: it does not seed tool calls, collect evidence,
    or parse a diagnosis. It exists so non-investigation surfaces (currently the
    interactive shell's tool-gathering pass) can call the *same* registered tools
    the investigation uses, with the same provider message shaping and context
    budgeting.

    ``on_event`` mirrors the investigation agent's callback contract so callers
    can render ``tool_start`` / ``tool_end`` activity live.
    """

    def _emit(kind: str, data: dict[str, Any]) -> None:
        if on_event is not None:
            try:
                on_event(kind, data)
            except Exception:  # noqa: BLE001 — event rendering must never break the loop
                logger.debug("[tool_loop] on_event(%s) raised; ignoring", kind, exc_info=True)

    tool_schemas = llm.tool_schemas(tools)
    ceiling = _context_budget_ceiling_for_model(getattr(llm, "_model", None))
    executed: list[tuple[ToolCall, Any]] = []
    final_text = ""
    hit_cap = True

    for iteration in range(max_iterations):
        _emit("llm_start", {"iteration": iteration})
        _enforce_context_budget(messages, system=system, tools=tool_schemas, ceiling=ceiling)
        response = llm.invoke(messages, system=system, tools=tool_schemas)
        messages.append(_build_assistant_msg(llm, response))

        if not response.has_tool_calls:
            final_text = response.content or ""
            hit_cap = False
            break

        for tc in response.tool_calls:
            _emit(
                "tool_start", {"id": tc.id, "name": tc.name, "input": _public_tool_input(tc.input)}
            )

        results = _run_parallel(response.tool_calls, tools, resolved_integrations)
        messages.extend(_build_tool_result_messages(llm, response.tool_calls, results))

        for tc, output in zip(response.tool_calls, results):
            executed.append((tc, output))
            _emit(
                "tool_end",
                {"id": tc.id, "name": tc.name, "output": redact_sensitive(output)},
            )

    return ToolLoopResult(
        messages=messages,
        final_text=final_text,
        executed=executed,
        hit_iteration_cap=hit_cap,
    )


__all__ = [
    "AgentEventCallback",
    "ToolLoopResult",
    "run_tool_calling_loop",
]
