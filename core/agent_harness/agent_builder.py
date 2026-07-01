"""Shared factory for building runtime :class:`~core.agent.Agent` instances.

Every agent harness surface (action, evidence, gateway) assembles the
per-turn configuration in a surface-specific factory, then hands it to
:func:`build_agent`. If :class:`~core.agent.Agent`'s constructor signature
changes, this file is the single edit site — all surfaces adopt the change
automatically.

Investigation stays a subclass of :class:`~core.agent.Agent` (it reuses
``_filter_tools`` and the event-emission plumbing) and constructs its own
loop, but the LLM/tools wiring it uses at ``run()`` start mirrors the
:class:`AgentConfig` fields so a future refactor can bring it under the same
roof without changing shape.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, TypeVar

from core.agent import Agent
from core.events import RuntimeEventCallback
from core.execution import ToolExecutionHooks
from core.types import RuntimeTool

# Pre-PEP-695 TypeVar so static analysers (CodeQL) recognise the type parameter
# rather than flagging ``RuntimeToolT`` in the return expression as an
# uninitialised local variable. Same bound as :class:`~core.agent.Agent`'s
# ``RuntimeToolT``.
RuntimeToolT = TypeVar("RuntimeToolT", bound=RuntimeTool)


@dataclass(frozen=True)
class AgentConfig:
    """Immutable per-turn config the runtime :class:`Agent` needs to construct.

    Surfaces assemble one of these and hand it to :func:`build_agent`.
    """

    llm: Any
    system: str
    tools: list[Any]
    resolved_integrations: dict[str, Any]
    max_iterations: int
    tool_resources: dict[str, Any] = field(default_factory=dict)
    tool_hooks: ToolExecutionHooks | None = None
    on_runtime_event: RuntimeEventCallback | None = None


def build_agent(config: AgentConfig) -> Agent[RuntimeToolT]:
    """Construct a runtime :class:`Agent` from an :class:`AgentConfig`.

    This is the single place :class:`Agent` is instantiated across the
    harness — surfaces call it after building their config.
    """
    return Agent[RuntimeToolT](
        llm=config.llm,
        system=config.system,
        tools=config.tools,
        resolved_integrations=config.resolved_integrations,
        max_iterations=config.max_iterations,
        tool_resources=config.tool_resources,
        tool_hooks=config.tool_hooks,
        on_runtime_event=config.on_runtime_event,
    )


__all__ = ["AgentConfig", "build_agent"]
