"""Unified per-turn agent runtime request.

Assembled once at the start of each turn by whichever surface is invoking the
runtime. Interactive shell fills the shell metadata fields; investigation can
leave them empty while still passing the same request object to ``Agent.run``.

Usage::

    agent_ctx = AgentContext.from_session(text, session)
    # pass agent_ctx to action agent + conversational assistant
    # keep passing session for writes (recording history, token usage, etc.)
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from context.conversation_history import MAX_CONVERSATION_MESSAGES
from context.models import ConversationMessage
from context.state import AgentModelInfo

RuntimeTool = Any

if TYPE_CHECKING:
    from config.llm_reasoning_effort import ReasoningEffortChoice
    from context.session import ReplSession
    from core.messages import RuntimeMessage


@dataclass(frozen=True)
class AgentContext:
    """Immutable request object passed from surfaces into the agent runtime.

    Shell-specific fields are optional metadata. Runtime-required fields are
    validated by ``core.Agent`` before a request is executed.
    """

    text: str
    """Raw user input text for this turn."""

    conversation_messages: tuple[ConversationMessage, ...]
    """Snapshot of recent CLI conversation as :class:`ConversationMessage`
    values, oldest first, capped to ``MAX_CONVERSATION_MESSAGES`` entries at
    assembly time."""

    configured_integrations: tuple[str, ...]
    """Integration names known to be configured at turn start."""

    configured_integrations_known: bool
    """Whether ``configured_integrations`` reflects real state (vs unknown)."""

    last_state: dict[str, Any] | None
    """Final ``AgentState`` from the most recent investigation (follow-up grounding)."""

    last_synthetic_observation_path: str | None
    """Path to latest synthetic-run observation file (failure explanation context)."""

    reasoning_effort: ReasoningEffortChoice | None
    """Session-scoped reasoning effort preference for LLM calls this turn."""

    system_prompt: str = ""
    """Runtime system prompt used by the shared agent loop."""

    available_tools: tuple[RuntimeTool, ...] = ()
    """All tools available to the surface for this turn."""

    active_tools: tuple[RuntimeTool, ...] = ()
    """Subset of tools offered to the model for this turn."""

    resolved_integrations: dict[str, Any] = field(default_factory=dict)
    """Resolved integration configuration passed to tool execution."""

    max_iterations: int = 1
    """Maximum runtime loop iterations for this request."""

    model: AgentModelInfo = field(default_factory=AgentModelInfo)
    """Model selection read model for request construction and diagnostics."""

    working_directory: str | None = None
    terminal_capabilities: dict[str, Any] = field(default_factory=dict)
    shell_command_context: dict[str, Any] = field(default_factory=dict)
    slash_command: str | None = None
    display_preferences: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_session(cls, text: str, session: ReplSession) -> AgentContext:
        """Snapshot the relevant session fields for one turn.

        Call this once at the top of ``ShellAgent.prompt`` before any
        mutations happen, then pass the returned context downstream.
        """
        messages = session.agent.messages
        snapshot: tuple[ConversationMessage, ...] = tuple(
            ConversationMessage.from_role_content(role, content)
            for role, content in messages[-MAX_CONVERSATION_MESSAGES:]
            if isinstance(role, str) and isinstance(content, str)
        )
        request_input = session.agent.select_agent_context_input(text)
        return cls(
            text=text,
            conversation_messages=snapshot,
            configured_integrations=tuple(session.configured_integrations),
            configured_integrations_known=bool(session.configured_integrations_known),
            last_state=session.last_state,
            last_synthetic_observation_path=session.last_synthetic_observation_path,
            reasoning_effort=session.reasoning_effort,
            system_prompt=request_input.system_prompt,
            available_tools=request_input.available_tools,
            active_tools=request_input.active_tools,
            resolved_integrations=request_input.resolved_integrations,
            max_iterations=request_input.max_iterations,
            model=request_input.model,
        )

    def runtime_messages(self) -> list[RuntimeMessage]:
        """Return the user message list expected by the runtime loop."""
        from core.messages import user_runtime_message

        return [user_runtime_message(self.text)]

    def validate_runtime_request(self) -> None:
        """Validate fields required once this object reaches ``Agent.run``."""
        if not self.system_prompt:
            raise ValueError("AgentContext.system_prompt is required for Agent.run().")
        if self.max_iterations < 1:
            raise ValueError("AgentContext.max_iterations must be positive.")
        if not self.active_tools:
            raise ValueError("AgentContext.active_tools must include at least one tool.")


__all__ = ["AgentContext"]
