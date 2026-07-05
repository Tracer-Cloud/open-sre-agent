"""Shared mutable agent state and immutable turn-request snapshots.

The per-session mutable store is reached through ``session.agent``
(``messages``, ``last_observation``, ``clear()``, and related accessors); the
snapshot models are read-only views used to assemble runtime requests.
"""

from __future__ import annotations

import contextlib
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Any, Literal

MAX_CONVERSATION_TURNS = 12
MAX_CONVERSATION_MESSAGES = MAX_CONVERSATION_TURNS * 2

AgentMessageRole = Literal["user", "assistant", "system", "tool"]
AgentRunStatus = Literal["idle", "running"]
type RuntimeTool = Any


class AgentStateError(ValueError):
    """Raised when an agent state action would violate an invariant."""


@dataclass(frozen=True)
class AgentModelInfo:
    """Runtime model selection read model."""

    provider: str | None = None
    name: str | None = None
    thinking_level: str | None = None


@dataclass(frozen=True)
class TurnRuntimeInput:
    """Selector output used to build a per-turn runtime request (``TurnSnapshot``)."""

    text: str
    messages: tuple[tuple[str, str], ...]
    system_prompt: str
    available_tools: tuple[RuntimeTool, ...]
    active_tools: tuple[RuntimeTool, ...]
    resolved_integrations: dict[str, Any]
    max_iterations: int
    model: AgentModelInfo
    last_observation: str | None


@dataclass(frozen=True)
class SessionAgentSnapshot:
    """Immutable state read model returned to consumers."""

    system_prompt: str
    model: AgentModelInfo
    available_tools: tuple[RuntimeTool, ...]
    active_tools: tuple[RuntimeTool, ...]
    messages: tuple[tuple[str, str], ...]
    last_observation: str | None
    run_status: AgentRunStatus
    pending_tool_calls: frozenset[str]
    resolved_integrations: dict[str, Any]
    max_iterations: int


@dataclass(frozen=True)
class AgentStateChange:
    """Post-commit change notification for subscribers."""

    action: str
    previous: SessionAgentSnapshot
    current: SessionAgentSnapshot


Subscriber = Callable[[AgentStateChange], None]


def _tool_name(tool: RuntimeTool) -> str:
    name = getattr(tool, "name", None)
    if not isinstance(name, str) or not name:
        raise AgentStateError("agent tools must expose a non-empty name.")
    return name


def _validate_unique_tools(tools: Sequence[RuntimeTool]) -> None:
    names = [_tool_name(tool) for tool in tools]
    duplicates = sorted({name for name in names if names.count(name) > 1})
    if duplicates:
        raise AgentStateError(f"duplicate tool names: {', '.join(duplicates)}")


class MutableAgentState:
    """Small Zustand-style store for agent-session state."""

    def __init__(
        self,
        *,
        system_prompt: str = "",
        model: AgentModelInfo | None = None,
        thinking_level: str | None = None,
        available_tools: Sequence[RuntimeTool] = (),
        active_tools: Sequence[RuntimeTool] | None = None,
        messages: Sequence[tuple[str, str]] = (),
        resolved_integrations: dict[str, Any] | None = None,
        max_iterations: int = 1,
    ) -> None:
        self._system_prompt = system_prompt
        base_model = model or AgentModelInfo()
        self._model = (
            base_model
            if thinking_level is None
            else AgentModelInfo(
                provider=base_model.provider,
                name=base_model.name,
                thinking_level=thinking_level,
            )
        )
        _validate_unique_tools(tuple(available_tools))
        self._available_tools = tuple(available_tools)
        selected_tools = tuple(self._available_tools if active_tools is None else active_tools)
        self._validate_active_subset(selected_tools, self._available_tools)
        self._active_tools = selected_tools
        self._messages = list(messages)
        self._last_observation: str | None = None
        self._run_status: AgentRunStatus = "idle"
        self._pending_tool_calls: set[str] = set()
        self._resolved_integrations = dict(resolved_integrations or {})
        self._max_iterations = max_iterations
        self._subscribers: list[Subscriber] = []

    @property
    def messages(self) -> list[tuple[str, str]]:
        """Compatibility accessor for existing session call sites."""
        return self._messages

    @messages.setter
    def messages(self, value: Sequence[tuple[str, str]]) -> None:
        self._commit("messages_update", lambda: self._replace_messages(value))

    @property
    def last_observation(self) -> str | None:
        return self._last_observation

    @last_observation.setter
    def last_observation(self, value: str | None) -> None:
        self._commit("observation_update", lambda: setattr(self, "_last_observation", value))

    @property
    def system_prompt(self) -> str:
        return self._system_prompt

    @property
    def model(self) -> AgentModelInfo:
        return self._model

    @property
    def available_tools(self) -> tuple[RuntimeTool, ...]:
        return self._available_tools

    @property
    def active_tools(self) -> tuple[RuntimeTool, ...]:
        return self._active_tools

    @property
    def resolved_integrations(self) -> dict[str, Any]:
        return dict(self._resolved_integrations)

    @property
    def max_iterations(self) -> int:
        return self._max_iterations

    @property
    def run_status(self) -> AgentRunStatus:
        return self._run_status

    def subscribe(self, subscriber: Subscriber) -> Callable[[], None]:
        self._subscribers.append(subscriber)

        def _unsubscribe() -> None:
            with contextlib.suppress(ValueError):
                self._subscribers.remove(subscriber)

        return _unsubscribe

    def snapshot(self) -> SessionAgentSnapshot:
        return SessionAgentSnapshot(
            system_prompt=self._system_prompt,
            model=self._model,
            available_tools=self._available_tools,
            active_tools=self._active_tools,
            messages=tuple(self._messages),
            last_observation=self._last_observation,
            run_status=self._run_status,
            pending_tool_calls=frozenset(self._pending_tool_calls),
            resolved_integrations=dict(self._resolved_integrations),
            max_iterations=self._max_iterations,
        )

    def select_messages(self) -> tuple[tuple[str, str], ...]:
        return tuple(self._messages)

    def select_turn_runtime_input(self, text: str) -> TurnRuntimeInput:
        return TurnRuntimeInput(
            text=text,
            messages=tuple(self._messages[-MAX_CONVERSATION_MESSAGES:]),
            system_prompt=self._system_prompt,
            available_tools=self._available_tools,
            active_tools=self._active_tools,
            resolved_integrations=dict(self._resolved_integrations),
            max_iterations=self._max_iterations,
            model=self._model,
            last_observation=self._last_observation,
        )

    def set_system_prompt(self, value: str) -> None:
        self._commit("system_prompt_update", lambda: setattr(self, "_system_prompt", value))

    def set_model(self, *, provider: str | None = None, name: str | None = None) -> None:
        self._commit(
            "model_update",
            lambda: setattr(
                self,
                "_model",
                AgentModelInfo(
                    provider=provider,
                    name=name,
                    thinking_level=self._model.thinking_level,
                ),
            ),
        )

    def set_thinking_level(self, value: str | None) -> None:
        self._commit(
            "thinking_level_update",
            lambda: setattr(
                self,
                "_model",
                AgentModelInfo(
                    provider=self._model.provider,
                    name=self._model.name,
                    thinking_level=value,
                ),
            ),
        )

    def set_available_tools(self, tools: Sequence[RuntimeTool]) -> None:
        next_tools = tuple(tools)
        _validate_unique_tools(next_tools)

        def _mutate() -> None:
            self._validate_active_subset(self._active_tools, next_tools)
            self._available_tools = next_tools

        self._commit("tools_update", _mutate)

    def set_active_tools(self, tools: Sequence[RuntimeTool]) -> None:
        next_tools = tuple(tools)
        self._validate_active_subset(next_tools, self._available_tools)
        self._commit("tools_update", lambda: setattr(self, "_active_tools", next_tools))

    def set_resolved_integrations(self, value: dict[str, Any]) -> None:
        self._commit(
            "resolved_integrations_update",
            lambda: setattr(self, "_resolved_integrations", dict(value)),
        )

    def set_max_iterations(self, value: int) -> None:
        if value < 1:
            raise AgentStateError("max_iterations must be positive.")
        self._commit("max_iterations_update", lambda: setattr(self, "_max_iterations", value))

    def record_turn(self, user_message: str, assistant_message: str) -> None:
        def _mutate() -> None:
            self._messages.append(("user", user_message))
            self._messages.append(("assistant", assistant_message))
            self._trim_messages()

        self._commit("messages_update", _mutate)

    def record_failure(self, user_message: str, error_text: str) -> None:
        self.record_turn(user_message, error_text)

    def reset_observation(self) -> None:
        self.last_observation = None

    def clear(self) -> None:
        def _mutate() -> None:
            self._messages.clear()
            self._last_observation = None
            self._pending_tool_calls.clear()
            self._run_status = "idle"

        self._commit("clear", _mutate)

    def begin_run(self) -> None:
        if self._run_status == "running":
            raise AgentStateError("agent run is already active.")
        self._commit("run_status_update", lambda: setattr(self, "_run_status", "running"))

    def end_run(self) -> None:
        if self._run_status != "running":
            raise AgentStateError("agent run is not active.")
        self._commit("run_status_update", lambda: setattr(self, "_run_status", "idle"))

    def mark_tool_pending(self, name: str) -> None:
        active_names = {_tool_name(tool) for tool in self._active_tools}
        if name not in active_names:
            raise AgentStateError(f"pending tool is not active: {name}")
        self._commit("run_status_update", lambda: self._pending_tool_calls.add(name))

    def clear_tool_pending(self, name: str) -> None:
        self._commit("run_status_update", lambda: self._pending_tool_calls.discard(name))

    def _replace_messages(self, messages: Sequence[tuple[str, str]]) -> None:
        self._messages = list(messages)
        self._trim_messages()

    def _trim_messages(self) -> None:
        if len(self._messages) > MAX_CONVERSATION_MESSAGES:
            self._messages[:] = self._messages[-MAX_CONVERSATION_MESSAGES:]

    def _commit(self, action: str, mutate: Callable[[], None]) -> None:
        previous = self.snapshot()
        mutate()
        current = self.snapshot()
        if previous == current:
            return
        change = AgentStateChange(action=action, previous=previous, current=current)
        for subscriber in tuple(self._subscribers):
            subscriber(change)

    @staticmethod
    def _validate_active_subset(
        active_tools: Sequence[RuntimeTool], available_tools: Sequence[RuntimeTool]
    ) -> None:
        _validate_unique_tools(active_tools)
        available_names = {_tool_name(tool) for tool in available_tools}
        missing = sorted(
            _tool_name(tool) for tool in active_tools if _tool_name(tool) not in available_names
        )
        if missing:
            raise AgentStateError(f"active tools are not available: {', '.join(missing)}")


def create_mutable_agent_state(**kwargs: Any) -> MutableAgentState:
    return MutableAgentState(**kwargs)


__all__ = [
    "TurnRuntimeInput",
    "AgentMessageRole",
    "AgentModelInfo",
    "AgentRunStatus",
    "AgentStateChange",
    "AgentStateError",
    "SessionAgentSnapshot",
    "MAX_CONVERSATION_MESSAGES",
    "MAX_CONVERSATION_TURNS",
    "MutableAgentState",
    "create_mutable_agent_state",
]
