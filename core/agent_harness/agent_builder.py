"""Shared factory for building runtime :class:`~core.agent.Agent` instances.

Each agent harness surface (action, evidence, gateway) assembles its per-turn
configuration in a surface-specific factory and hands it to :func:`build_agent`,
the single construction site for :class:`~core.agent.Agent` across surfaces.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Generic, TypeVar

from core.agent import Agent
from core.events import RuntimeEventCallback
from core.execution import ToolExecutionHooks
from core.llm.types import AgentLLMClient, ResolvedIntegrations
from core.provider import ProviderHooks
from core.types import RuntimeTool

RuntimeToolT = TypeVar("RuntimeToolT", bound=RuntimeTool)


@dataclass(frozen=True)
class AgentConfig(Generic[RuntimeToolT]):  # noqa: UP046
    """Immutable per-turn config the runtime :class:`Agent` needs to construct.

    Surfaces assemble one of these and hand it to :func:`build_agent`.
    """

    llm: AgentLLMClient
    system: str
    tools: tuple[RuntimeToolT, ...]
    resolved_integrations: ResolvedIntegrations
    max_iterations: int
    tool_resources: dict[str, Any] = field(default_factory=dict)
    tool_hooks: ToolExecutionHooks | None = None
    on_runtime_event: RuntimeEventCallback | None = None
    agent_cls: type[Agent[RuntimeToolT]] = Agent
    """Override to construct a caller-defined ``Agent`` subclass instead of the
    base class — e.g. a surface that needs to override a hook like
    ``_should_accept_conclusion``. Defaults to :class:`Agent` so existing
    callers are unaffected."""
    provider_hooks: ProviderHooks | None = None
    """Override the message-conversion/request seams (``Agent.__init__``'s
    ``provider_hooks``) — e.g. a surface that needs its own context-budget
    eviction markers to survive the default provider-message conversion.
    Defaults to ``None`` (``Agent``'s own default), so existing callers are
    unaffected."""


def build_agent(  # noqa: UP047
    config: AgentConfig[RuntimeToolT],
) -> Agent[RuntimeToolT]:
    """Construct a runtime :class:`Agent` from an :class:`AgentConfig`.

    This is the single place :class:`Agent` (or a caller-supplied subclass via
    ``config.agent_cls``) is instantiated across the harness — surfaces call
    it after building their config.
    """
    return config.agent_cls(
        llm=config.llm,
        system=config.system,
        tools=config.tools,
        resolved_integrations=config.resolved_integrations,
        max_iterations=config.max_iterations,
        tool_resources=config.tool_resources,
        tool_hooks=config.tool_hooks,
        on_runtime_event=config.on_runtime_event,
        provider_hooks=config.provider_hooks,
    )


__all__ = ["AgentConfig", "build_agent"]
