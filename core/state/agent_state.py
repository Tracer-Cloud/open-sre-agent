"""Cross-turn agent state: the conversation transcript and last observation.

``session.agent`` is a :class:`MutableAgentState` — the mutable state that
persists *across* turns. Production reads and writes ``messages`` (transcript),
``last_observation``, and ``clear()`` only. Everything a single turn needs
(tools, resolved integrations, system prompt, iteration cap) lives on
``TurnSnapshot``, not here.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Literal

MAX_CONVERSATION_TURNS = 12
MAX_CONVERSATION_MESSAGES = MAX_CONVERSATION_TURNS * 2

AgentMessageRole = Literal["user", "assistant", "system", "tool"]


class MutableAgentState:
    """Cross-turn agent state: the conversation transcript and last observation.

    Holds only what must survive across turns. Per-turn data (tools, resolved
    integrations, system prompt, iteration cap) lives on ``TurnSnapshot``.
    """

    def __init__(self, *, messages: Sequence[tuple[str, str]] = ()) -> None:
        self._messages: list[tuple[str, str]] = list(messages)
        self._last_observation: str | None = None

    @property
    def messages(self) -> list[tuple[str, str]]:
        return self._messages

    @messages.setter
    def messages(self, value: Sequence[tuple[str, str]]) -> None:
        self._replace_messages(value)

    @property
    def last_observation(self) -> str | None:
        return self._last_observation

    @last_observation.setter
    def last_observation(self, value: str | None) -> None:
        self._last_observation = value

    def record_turn(self, user_message: str, assistant_message: str) -> None:
        self._messages.append(("user", user_message))
        self._messages.append(("assistant", assistant_message))
        self._trim_messages()

    def reset_observation(self) -> None:
        self._last_observation = None

    def clear(self) -> None:
        self._messages.clear()
        self._last_observation = None

    def _replace_messages(self, messages: Sequence[tuple[str, str]]) -> None:
        self._messages = list(messages)
        self._trim_messages()

    def _trim_messages(self) -> None:
        if len(self._messages) > MAX_CONVERSATION_MESSAGES:
            self._messages[:] = self._messages[-MAX_CONVERSATION_MESSAGES:]


__all__ = [
    "MAX_CONVERSATION_MESSAGES",
    "MAX_CONVERSATION_TURNS",
    "AgentMessageRole",
    "MutableAgentState",
]
