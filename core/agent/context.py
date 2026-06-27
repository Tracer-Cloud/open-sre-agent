"""Per-turn immutable context snapshot for the agentic turn engine.

Assembled once at the start of each turn from any object satisfying
:class:`TurnContextSource` (the interactive shell passes its ``ReplSession``;
headless callers pass an in-memory session store). All fields reflect session
state at turn-start and do not change while the turn runs, so downstream code
reads a stable snapshot rather than a live, concurrently-mutated object.

Usage::

    turn_ctx = TurnContext.from_session(text, session)
    # pass turn_ctx to action agent + conversational assistant
    # keep passing the live session for writes (history, token usage, etc.)
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable

from core.agent.conversation_history import MAX_CONVERSATION_MESSAGES

if TYPE_CHECKING:
    from config.llm_reasoning_effort import ReasoningEffortChoice


@runtime_checkable
class TurnContextSource(Protocol):
    """Structural source of per-turn snapshot fields.

    ``ReplSession`` satisfies this without inheriting it; headless session
    stores implement the same attributes. Keeping this structural is what lets
    ``agent/`` build a ``TurnContext`` without importing ``interactive_shell``.
    """

    cli_agent_messages: list[tuple[str, str]]
    configured_integrations_known: bool
    last_state: dict[str, Any] | None
    last_synthetic_observation_path: str | None
    reasoning_effort: ReasoningEffortChoice | None

    # Read-only here; ``ReplSession`` stores a tuple. A property matches
    # covariantly, so any concrete ``Sequence[str]`` implementation satisfies it.
    @property
    def configured_integrations(self) -> Sequence[str]: ...


@dataclass(frozen=True)
class TurnContext:
    """Immutable per-turn snapshot assembled from ``ReplSession`` at turn start.

    Carries everything the action agent and conversational assistant need to
    build prompts and ground answers, frozen at the moment the turn begins.

    The live ``ReplSession`` is still passed separately to callers that need
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

    @classmethod
    def from_session(cls, text: str, session: TurnContextSource) -> TurnContext:
        """Snapshot the relevant session fields for one turn.

        Call this once at the top of the turn before any mutations happen, then
        pass the returned context downstream. ``session`` is anything satisfying
        :class:`TurnContextSource` (e.g. the shell's ``ReplSession``).
        """
        messages = session.cli_agent_messages
        snapshot: tuple[tuple[str, str], ...] = tuple(
            (str(role), str(content))
            for role, content in messages[-MAX_CONVERSATION_MESSAGES:]
            if isinstance(role, str) and isinstance(content, str)
        )
        return cls(
            text=text,
            conversation_messages=snapshot,
            configured_integrations=tuple(session.configured_integrations),
            configured_integrations_known=bool(session.configured_integrations_known),
            last_state=session.last_state,
            last_synthetic_observation_path=session.last_synthetic_observation_path,
            reasoning_effort=session.reasoning_effort,
        )


__all__ = ["TurnContext"]
