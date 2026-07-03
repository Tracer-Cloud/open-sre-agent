"""Per-turn immutable context snapshot for the agentic turn engine.

Built once at turn start via :meth:`TurnContext.from_session`. Downstream
prompt builders read this snapshot; the live session is still used for writes.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable

from core.agent_harness.prompts.conversation_memory import MAX_CONVERSATION_MESSAGES

if TYPE_CHECKING:
    from config.llm_reasoning_effort import ReasoningEffortChoice
    from core.messages import RuntimeMessage

RuntimeTool = Any


@runtime_checkable
class PromptRenderable(Protocol):
    """Structured prompt object that can render itself into provider text."""

    def render(self) -> str:
        raise NotImplementedError


type SystemPromptInput = str | PromptRenderable


@runtime_checkable
class TurnContextSource(Protocol):
    """Structural source of per-turn snapshot fields.

    ``Session`` satisfies this without inheriting it; headless session
    stores implement the same attributes. Keeping this structural is what lets
    ``agent/`` build a ``TurnContext`` without importing ``interactive_shell``.
    """

    cli_agent_messages: list[tuple[str, str]]
    configured_integrations_known: bool
    last_state: dict[str, Any] | None
    last_synthetic_observation_path: str | None
    reasoning_effort: ReasoningEffortChoice | None

    # Read-only here; ``Session`` stores a tuple. A property matches
    # covariantly, so any concrete ``Sequence[str]`` implementation satisfies it.
    @property
    def configured_integrations(self) -> Sequence[str]:
        raise NotImplementedError


@runtime_checkable
class AgentRuntimeRequest(Protocol):
    """Runtime request contract consumed by ``core.agent.Agent``."""

    system_prompt: Any
    active_tools: Sequence[RuntimeTool]
    resolved_integrations: dict[str, Any]
    tool_resources: dict[str, Any]
    max_iterations: int

    def render_system_prompt(self) -> str:
        raise NotImplementedError

    def runtime_messages(self) -> list[RuntimeMessage]:
        raise NotImplementedError

    def validate_runtime_request(self) -> None:
        raise NotImplementedError


def _render_system_prompt(prompt: SystemPromptInput) -> str:
    if isinstance(prompt, str):
        return prompt
    rendered = prompt.render()
    if not isinstance(rendered, str):
        raise TypeError("system_prompt.render() must return str.")
    return rendered


def _select_runtime_request_input(text: str, source: Any) -> Any | None:
    """Read optional runtime-request fields from a structural session source."""
    direct_selector = getattr(source, "select_agent_context_input", None)
    if callable(direct_selector):
        return direct_selector(text)

    agent_state = getattr(source, "agent", None)
    state_selector = getattr(agent_state, "select_agent_context_input", None)
    if callable(state_selector):
        return state_selector(text)

    return None


@dataclass(frozen=True)
class TurnContext:
    """Immutable per-turn snapshot and optional runtime request.

    Carries everything the action agent and conversational assistant need to
    build prompts and ground answers, frozen at the moment the turn begins. It
    can also carry the runtime loop request fields that ``Agent.run`` needs.

    The live ``Session`` is still passed separately to callers that need
    to write state (recording history, persisting token usage, updating intent).
    """

    text: str
    """Raw user input text for this turn."""

    conversation_messages: tuple[tuple[str, str], ...]
    """Snapshot of recent CLI conversation: ``(role, content)`` pairs, oldest
    first, capped to ``MAX_CONVERSATION_MESSAGES`` entries at assembly time."""

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

    system_prompt: SystemPromptInput = ""
    """Runtime system prompt used by the shared agent loop."""

    available_tools: tuple[RuntimeTool, ...] = ()
    """All tools available to the surface for this turn."""

    active_tools: tuple[RuntimeTool, ...] = ()
    """Subset of tools offered to the model for this turn."""

    resolved_integrations: dict[str, Any] = field(default_factory=dict)
    """Resolved integration configuration passed to tool execution."""

    tool_resources: dict[str, Any] = field(default_factory=dict)
    """Non-serializable runtime resources made available to opted-in tools."""

    max_iterations: int = 1
    """Maximum runtime loop iterations for this request."""

    model: Any | None = None
    """Optional model selection read model for diagnostics."""

    working_directory: str | None = None
    terminal_capabilities: dict[str, Any] = field(default_factory=dict)
    shell_command_context: dict[str, Any] = field(default_factory=dict)
    slash_command: str | None = None
    display_preferences: dict[str, Any] = field(default_factory=dict)
    last_observation: str | None = None

    @classmethod
    def from_session(cls, text: str, session: TurnContextSource) -> TurnContext:
        """Snapshot the relevant session fields for one turn.

        Call this once at the top of the turn before any mutations happen, then
        pass the returned context downstream. ``session`` is anything satisfying
        :class:`TurnContextSource` (e.g. the shell's ``Session``). When the
        source also exposes ``select_agent_context_input`` directly or through
        ``source.agent``, runtime request fields are snapshotted too.
        """
        messages = session.cli_agent_messages
        snapshot: tuple[tuple[str, str], ...] = tuple(
            (str(role), str(content))
            for role, content in messages[-MAX_CONVERSATION_MESSAGES:]
            if isinstance(role, str) and isinstance(content, str)
        )
        runtime_input = _select_runtime_request_input(text, session)
        last_observation = _read_last_observation(session, runtime_input)
        return cls(
            text=text,
            conversation_messages=snapshot,
            configured_integrations=tuple(session.configured_integrations),
            configured_integrations_known=bool(session.configured_integrations_known),
            last_state=session.last_state,
            last_synthetic_observation_path=session.last_synthetic_observation_path,
            reasoning_effort=session.reasoning_effort,
            system_prompt=getattr(runtime_input, "system_prompt", ""),
            available_tools=tuple(getattr(runtime_input, "available_tools", ())),
            active_tools=tuple(getattr(runtime_input, "active_tools", ())),
            resolved_integrations=dict(getattr(runtime_input, "resolved_integrations", {}) or {}),
            tool_resources=dict(getattr(runtime_input, "tool_resources", {}) or {}),
            max_iterations=int(getattr(runtime_input, "max_iterations", 1)),
            model=getattr(runtime_input, "model", None),
            last_observation=last_observation,
        )

    def render_system_prompt(self) -> str:
        """Render the runtime system prompt to provider-ready text."""
        return _render_system_prompt(self.system_prompt)

    def runtime_messages(self) -> list[RuntimeMessage]:
        """Return the user message list expected by the runtime loop."""
        from core.messages import UserRuntimeMessage

        return [UserRuntimeMessage(content=self.text)]

    def validate_runtime_request(self) -> None:
        """Validate fields required once this object reaches ``Agent.run``."""
        if not self.render_system_prompt():
            raise ValueError("TurnContext.system_prompt is required for Agent.run().")
        if self.max_iterations < 1:
            raise ValueError("TurnContext.max_iterations must be positive.")
        if not self.active_tools:
            raise ValueError("TurnContext.active_tools must include at least one tool.")


def _read_last_observation(session: TurnContextSource, runtime_input: Any | None) -> str | None:
    """Read the last tool observation from runtime input or the live session."""
    from_runtime = getattr(runtime_input, "last_observation", None)
    if isinstance(from_runtime, str) and from_runtime.strip():
        return from_runtime

    agent = getattr(session, "agent", None)
    agent_observation = getattr(agent, "last_observation", None)
    if isinstance(agent_observation, str) and agent_observation.strip():
        return agent_observation

    session_observation = getattr(session, "last_command_observation", None)
    if isinstance(session_observation, str) and session_observation.strip():
        return session_observation

    return None


__all__ = [
    "AgentRuntimeRequest",
    "PromptRenderable",
    "TurnContext",
    "TurnContextSource",
]
