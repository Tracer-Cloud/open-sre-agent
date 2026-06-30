"""Tool execution helpers for the shared LLM tool-calling runtime."""

from __future__ import annotations

import json
import logging
from collections.abc import Callable, Sequence
from concurrent.futures import Future, ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field, replace
from typing import Any

from core.llm.types import ToolCall
from core.types import AgentTool, AgentToolContext, RuntimeTool
from platform.observability.tool_trace import redact_sensitive
from tools.utils.integration_sources import availability_view

logger = logging.getLogger(__name__)

_TOOL_EXECUTOR_WORKERS = 10
_UNSET: object = object()
_INJECTED_CREDENTIAL_KEYS = frozenset(
    {
        "github_url",
        "github_mode",
        "github_token",
        "github_command",
        "github_args",
    }
)


@dataclass(frozen=True)
class ToolExecutionResult:
    """Structured result from one tool call."""

    content: str | list[dict[str, Any]]
    details: Any = None
    is_error: bool = False
    terminate: bool = False
    metadata: dict[str, Any] = field(default_factory=dict)

    def provider_content(self) -> str | list[dict[str, Any]]:
        """Return the content that should be sent back to the LLM provider."""
        return self.content

    def compat_payload(self) -> Any:
        """Return the historical raw payload shape used by old call sites."""
        if self.is_error:
            return {"error": self.content}
        return self.details if self.details is not None else self.content


@dataclass(frozen=True)
class ToolExecutionRequest:
    """Validated tool-call data passed to execution hooks."""

    tool_call: ToolCall
    tool: RuntimeTool
    arguments: dict[str, Any]
    source: str
    resolved_integrations: dict[str, Any]


@dataclass(frozen=True)
class ToolExecutionPatch:
    """Patch object returned by ``after_tool_call`` hooks."""

    content: str | list[dict[str, Any]] | None = None
    details: Any = _UNSET
    is_error: bool | None = None
    terminate: bool | None = None
    metadata: dict[str, Any] | None = None


@dataclass(frozen=True)
class BeforeToolCallResult:
    """Decision object returned by ``before_tool_call`` hooks."""

    approved: bool = False
    blocked: bool = False
    reason: str = ""
    details: Any = None
    terminate: bool = False
    metadata: dict[str, Any] = field(default_factory=dict)


BeforeToolCallHook = Callable[[ToolExecutionRequest], BeforeToolCallResult | None]
AfterToolCallHook = Callable[[ToolExecutionRequest, ToolExecutionResult], ToolExecutionPatch | None]
ToolUpdateHook = Callable[[ToolExecutionRequest, Any], None]


@dataclass(frozen=True)
class ToolExecutionHooks:
    """Lifecycle hooks around validated runtime tool execution."""

    before_tool_call: BeforeToolCallHook | None = None
    after_tool_call: AfterToolCallHook | None = None
    on_tool_update: ToolUpdateHook | None = None


def execute_tool_calls(
    tool_calls: list[ToolCall],
    tools: Sequence[RuntimeTool],
    resolved_integrations: dict[str, Any],
    *,
    hooks: ToolExecutionHooks | None = None,
    tool_resources: dict[str, Any] | None = None,
) -> list[ToolExecutionResult]:
    """Execute provider-requested tools and return structured results.

    Arguments are validated before execution. A single sequential tool in the
    batch forces the whole batch to run sequentially; otherwise calls run in
    parallel while preserving provider order in the returned list.
    """

    hooks = hooks or ToolExecutionHooks()
    tool_sources = availability_view(resolved_integrations)
    tool_map = {t.name: t for t in tools}
    runtime_resources = dict(tool_resources or {})

    def _call(tc: ToolCall) -> ToolExecutionResult:
        tool = tool_map.get(tc.name)
        if tool is None:
            return _error_result(f"unknown tool: {tc.name}", metadata={"tool_name": tc.name})

        try:
            validation_error = tool.validate_public_input(tc.input)
            if validation_error:
                return _error_result(validation_error, metadata={"tool_name": tc.name})

            request = ToolExecutionRequest(
                tool_call=tc,
                tool=tool,
                arguments=dict(tc.input),
                source=tool_source(tools, tc.name),
                resolved_integrations=resolved_integrations,
            )
            before = _run_before_hook(hooks, request)
            if before is not None and before.blocked:
                return ToolExecutionResult(
                    content=before.reason or f"{tc.name} blocked by before_tool_call hook.",
                    details=before.details,
                    is_error=True,
                    terminate=before.terminate,
                    metadata={"tool_name": tc.name, **before.metadata},
                )

            if isinstance(tool, AgentTool):
                context = AgentToolContext(
                    resolved_integrations=resolved_integrations,
                    resources=runtime_resources,
                    _emit_update=lambda update: _run_update_hook(hooks, request, update),
                )
                raw = tool.execute(tc.input, context)
            else:
                injected = tool.extract_params(tool_sources)
                kwargs = {**injected, **tc.input}
                for key, value in injected.items():
                    if key in _INJECTED_CREDENTIAL_KEYS and value not in (None, "", []):
                        kwargs[key] = value
                if getattr(tool, "accepts_runtime_context", False):
                    context = AgentToolContext(
                        resolved_integrations=resolved_integrations,
                        resources=runtime_resources,
                        _emit_update=lambda update: _run_update_hook(hooks, request, update),
                    )
                    raw = tool.run(**kwargs, context=context)
                else:
                    raw = tool.run(**kwargs)
            result = _normalize_result(raw, tool_name=tc.name)
            patch = _run_after_hook(hooks, request, result)
            if patch is not None:
                result = _apply_patch(result, patch)
            return result
        except Exception as exc:
            logger.warning("[tool:%s] failed: %s", tc.name, exc)
            return _error_result(str(exc), metadata={"tool_name": tc.name})

    if len(tool_calls) == 1 or _requires_sequential_execution(tool_calls, tool_map):
        return [_call(tc) for tc in tool_calls]

    results: list[ToolExecutionResult | object] = [_UNSET] * len(tool_calls)
    submitted: dict[Future[ToolExecutionResult], int] = {}
    try:
        with ThreadPoolExecutor(max_workers=min(_TOOL_EXECUTOR_WORKERS, len(tool_calls))) as pool:
            for i, tc in enumerate(tool_calls):
                submitted[pool.submit(_call, tc)] = i
            for fut in as_completed(submitted):
                try:
                    results[submitted[fut]] = fut.result()
                except Exception as fut_exc:  # noqa: BLE001  # lgtm[py/catch-base-exception]
                    results[submitted[fut]] = _error_result(str(fut_exc))
    except RuntimeError as exc:
        logger.warning("[execute_tools] RuntimeError – falling back to sequential: %s", exc)
        for fut, i in submitted.items():
            if results[i] is _UNSET and fut.done():
                try:
                    results[i] = fut.result()
                except Exception as fut_exc:  # noqa: BLE001  # lgtm[py/catch-base-exception]
                    results[i] = _error_result(str(fut_exc))
        for i, tc in enumerate(tool_calls):
            if results[i] is _UNSET:
                results[i] = _call(tc)
    return [
        r if isinstance(r, ToolExecutionResult) else _error_result("tool did not run")
        for r in results
    ]


def execute_tools(
    tool_calls: list[ToolCall],
    tools: Sequence[RuntimeTool],
    resolved_integrations: dict[str, Any],
    *,
    on_tool_update: Callable[[ToolCall, Any], None] | None = None,
) -> list[Any]:
    """Compatibility wrapper returning historical raw payloads."""

    hooks: ToolExecutionHooks | None = None
    if on_tool_update is not None:

        def _on_update(request: ToolExecutionRequest, update: Any) -> None:
            on_tool_update(request.tool_call, update)

        hooks = ToolExecutionHooks(on_tool_update=_on_update)
    return [
        result.compat_payload()
        for result in execute_tool_calls(
            tool_calls,
            tools,
            resolved_integrations,
            hooks=hooks,
            tool_resources=None,
        )
    ]


def _requires_sequential_execution(
    tool_calls: list[ToolCall],
    tool_map: dict[str, RuntimeTool],
) -> bool:
    for tc in tool_calls:
        tool = tool_map.get(tc.name)
        if isinstance(tool, AgentTool) and tool.effective_execution_mode == "sequential":
            return True
        if (
            not isinstance(tool, AgentTool)
            and tool is not None
            and not getattr(tool, "parallel_safe", True)
        ):
            return True
    return False


def _normalize_result(raw: Any, *, tool_name: str) -> ToolExecutionResult:
    if isinstance(raw, ToolExecutionResult):
        return raw
    is_error = isinstance(raw, dict) and "error" in raw
    content = _content_from_payload(raw)
    if is_error:
        content = str(raw.get("error", content))
    return ToolExecutionResult(
        content=content,
        details=raw,
        is_error=is_error,
        metadata={"tool_name": tool_name},
    )


def _content_from_payload(raw: Any) -> str | list[dict[str, Any]]:
    if isinstance(raw, str):
        return raw
    if isinstance(raw, list) and all(isinstance(item, dict) for item in raw):
        return raw
    return json.dumps(raw, default=str)


def _error_result(message: str, *, metadata: dict[str, Any] | None = None) -> ToolExecutionResult:
    return ToolExecutionResult(
        content=message,
        details={"error": message},
        is_error=True,
        metadata=dict(metadata or {}),
    )


def _run_before_hook(
    hooks: ToolExecutionHooks,
    request: ToolExecutionRequest,
) -> BeforeToolCallResult | None:
    if hooks.before_tool_call is None:
        return None
    try:
        return hooks.before_tool_call(request)
    except Exception as exc:  # noqa: BLE001 - lifecycle hooks should fail closed for the call
        logger.warning("[tool:%s] before_tool_call failed: %s", request.tool_call.name, exc)
        return BeforeToolCallResult(blocked=True, reason=str(exc))


def _run_after_hook(
    hooks: ToolExecutionHooks,
    request: ToolExecutionRequest,
    result: ToolExecutionResult,
) -> ToolExecutionPatch | None:
    if hooks.after_tool_call is None:
        return None
    try:
        return hooks.after_tool_call(request, result)
    except Exception:  # noqa: BLE001 - observer failures must not corrupt the transcript
        logger.debug(
            "[tool:%s] after_tool_call raised; ignoring",
            request.tool_call.name,
            exc_info=True,
        )
        return None


def _run_update_hook(
    hooks: ToolExecutionHooks,
    request: ToolExecutionRequest,
    update: Any,
) -> None:
    if hooks.on_tool_update is None:
        return
    try:
        hooks.on_tool_update(request, update)
    except Exception:  # noqa: BLE001 - partial rendering must not break tool execution
        logger.debug(
            "[tool:%s] on_tool_update raised; ignoring",
            request.tool_call.name,
            exc_info=True,
        )


def _apply_patch(result: ToolExecutionResult, patch: ToolExecutionPatch) -> ToolExecutionResult:
    metadata = dict(result.metadata)
    if patch.metadata:
        metadata.update(patch.metadata)
    kwargs: dict[str, Any] = {"metadata": metadata}
    if patch.content is not None:
        kwargs["content"] = patch.content
    if patch.details is not _UNSET:
        kwargs["details"] = patch.details
    if patch.is_error is not None:
        kwargs["is_error"] = patch.is_error
    if patch.terminate is not None:
        kwargs["terminate"] = patch.terminate
    return replace(result, **kwargs)


def public_tool_input(value: dict[str, Any]) -> dict[str, Any]:
    redacted = redact_sensitive(value)
    return {
        key: item
        for key, item in redacted.items()
        if item != "[runtime object]" and item != "[redacted]"
    }


def tool_source(tools: Sequence[RuntimeTool], tool_name: str) -> str:
    for tool in tools:
        if tool.name == tool_name:
            return str(getattr(tool, "source", "unknown"))
    return "unknown"


def summarise(output: Any) -> str:
    if isinstance(output, ToolExecutionResult):
        output = output.compat_payload()
    if isinstance(output, dict) and "error" in output:
        return f"error: {output['error']}"
    text = json.dumps(output, default=str)
    return text[:120] + "..." if len(text) > 120 else text
