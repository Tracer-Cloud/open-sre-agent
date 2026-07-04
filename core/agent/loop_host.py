"""The callback contract the ReAct loop drives.

``run_react_loop`` depends only on this ``AgentLoopHost`` and an
``AgentRunInput`` — any object implementing these methods can drive the loop.
"""

from __future__ import annotations

from typing import Any, Protocol

from core.events import RuntimeEvent
from core.execution import ToolExecutionHooks
from core.messages import ProviderMessage, RuntimeMessage
from core.provider import ProviderRequest
from core.types import RuntimeTool


class AgentLoopHost[RuntimeToolT: RuntimeTool](Protocol):
    """The narrow set of hooks ``run_react_loop`` calls back into.

    ``core.agent.Agent`` implements this via ``EventEmitterMixin``,
    ``ToolFilterMixin``, ``SteeringMixin`` (``core.agent.mixins``), and its own
    ``_should_accept_conclusion`` override hook plus thin ``AgentProviderHookDelegate``
    forwarders (``_transform_context``/``_convert_to_llm``/``_before_request``/
    ``_after_response``). The provider-hook delegate's concrete type is
    deliberately *not* part of this contract — only the method calls are —
    so a host can wire the four seams however it likes.
    """

    _tool_hooks: ToolExecutionHooks

    def _filter_tools(self, tools: list[RuntimeToolT]) -> list[RuntimeToolT]:
        pass

    def _emit_runtime(self, event: RuntimeEvent) -> None:
        pass

    def _drain_steering_messages(self, messages: list[RuntimeMessage]) -> None:
        pass

    def _pop_follow_up_message(self) -> str | None:
        pass

    def _should_accept_conclusion(
        self, *, evidence_count: int, iteration: int
    ) -> tuple[bool, str | None]:
        pass

    def _transform_context(self, messages: list[RuntimeMessage]) -> list[RuntimeMessage]:
        pass

    def _convert_to_llm(self, llm: Any, messages: list[RuntimeMessage]) -> list[ProviderMessage]:
        pass

    def _before_request(self, request: ProviderRequest) -> ProviderRequest:
        pass

    def _after_response(self, request: ProviderRequest, response: Any) -> Any:
        pass


__all__ = ["AgentLoopHost"]
