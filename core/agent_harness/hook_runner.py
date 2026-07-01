"""Run one turn using a ``SurfaceHooks`` bundle.

Ties the four surface hooks (``resolve_tools``, ``construct_prompt``,
``inject_context``, ``route_response``) to ``Agent.run`` so a surface can
drive a turn end-to-end via the hook contract, without instantiating an
``Agent`` subclass.

- ``resolve_tools`` supplies the tool list.
- ``construct_prompt`` supplies the system prompt.
- ``inject_context`` is wired through ``ProviderHooks.before_provider_request``
  so it sees every per-iteration provider request before dispatch.
- ``route_response`` is called once with the final assistant text.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from core.agent import Agent, AgentRunResult
from core.agent_harness.surface_hooks import SurfaceHooks, assert_hooks_complete
from core.provider import ProviderHooks, ProviderRequest

if TYPE_CHECKING:
    from core.agent_harness.turn_context import TurnContext
    from core.events import RuntimeEventCallback
    from core.execution import ToolExecutionHooks


def _provider_hooks_from_inject_context(
    hooks: SurfaceHooks,
    ctx: TurnContext | None,
) -> ProviderHooks:
    """Wrap ``hooks.inject_context`` in a ``ProviderHooks`` slot.

    ``Agent.run`` calls ``before_provider_request(request) -> request`` per
    iteration; the surface hook takes ``(ctx, request)``. Bind ``ctx`` here.
    """

    def _before_provider_request(request: ProviderRequest) -> ProviderRequest:
        return hooks.inject_context(ctx, request)  # type: ignore[arg-type]

    return ProviderHooks(before_provider_request=_before_provider_request)


def run_turn_via_hooks(
    *,
    message: str,
    hooks: SurfaceHooks,
    llm: Any,
    ctx: TurnContext | None = None,
    resolved_integrations: dict[str, Any] | None = None,
    max_iterations: int = 10,
    tool_hooks: ToolExecutionHooks | None = None,
    tool_resources: dict[str, Any] | None = None,
    on_runtime_event: RuntimeEventCallback | None = None,
    initial_messages: list[dict[str, Any]] | None = None,
) -> AgentRunResult:
    """Drive one ``Agent`` turn from a ``SurfaceHooks`` bundle.

    ``message`` becomes the sole user runtime message when ``initial_messages``
    is not supplied; callers with a pre-built history (e.g. transformed
    action-agent user text) pass ``initial_messages`` directly. ``llm`` is
    forwarded to ``Agent`` — the hook contract doesn't cover LLM selection
    today. ``tool_resources`` and ``on_runtime_event`` reach ``Agent`` as-is.
    Returns the full :class:`AgentRunResult`.
    """
    assert_hooks_complete(hooks)

    tools = hooks.resolve_tools(ctx)  # type: ignore[arg-type]
    system = hooks.construct_prompt(ctx)  # type: ignore[arg-type]
    provider_hooks = _provider_hooks_from_inject_context(hooks, ctx)

    agent = Agent(
        llm=llm,
        system=system,
        tools=tools,
        resolved_integrations=dict(resolved_integrations or {}),
        max_iterations=max_iterations,
        tool_hooks=tool_hooks,
        tool_resources=dict(tool_resources or {}),
        on_runtime_event=on_runtime_event,
        provider_hooks=provider_hooks,
    )
    initial = initial_messages or [{"role": "user", "content": message}]
    result = agent.run(initial_messages=initial)

    hooks.route_response(ctx, result.final_text)  # type: ignore[arg-type]
    return result
