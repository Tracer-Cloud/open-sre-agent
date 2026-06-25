"""Tool execution helpers for the shared LLM tool-calling runtime."""

from __future__ import annotations

import json
import logging
from concurrent.futures import Future, ThreadPoolExecutor, as_completed
from typing import Any

from services.agent_llm_client import ToolCall
from tools.registered_tool import RegisteredTool
from tools.utils.integration_sources import availability_view
from platform.observability.tool_trace import redact_sensitive

logger = logging.getLogger(__name__)

_TOOL_EXECUTOR_WORKERS = 10
_UNSET: object = object()  # sentinel distinguishing "not yet started" from a None tool result


def execute_tools(
    tool_calls: list[ToolCall],
    tools: list[RegisteredTool],
    resolved_integrations: dict[str, Any],
) -> list[Any]:
    tool_sources = availability_view(resolved_integrations)
    tool_map = {t.name: t for t in tools}

    def _call(tc: ToolCall) -> Any:
        tool = tool_map.get(tc.name)
        if tool is None:
            return {"error": f"unknown tool: {tc.name}"}
        try:
            validation_error = tool.validate_public_input(tc.input)
            if validation_error:
                return {"error": validation_error}
            injected = tool.extract_params(tool_sources)
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
        logger.warning("[execute_tools] RuntimeError – falling back to sequential: %s", exc)
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


def public_tool_input(value: dict[str, Any]) -> dict[str, Any]:
    redacted = redact_sensitive(value)
    return {
        key: item
        for key, item in redacted.items()
        if item != "[runtime object]" and item != "[redacted]"
    }


def tool_source(tools: list[RegisteredTool], tool_name: str) -> str:
    for tool in tools:
        if tool.name == tool_name:
            return str(tool.source)
    return "unknown"


def summarise(output: Any) -> str:
    if isinstance(output, dict) and "error" in output:
        return f"error: {output['error']}"
    text = json.dumps(output, default=str)
    return text[:120] + "…" if len(text) > 120 else text
