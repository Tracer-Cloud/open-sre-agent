"""Equivalence tests for the ``ActionAgent`` -> ``SurfaceHooks`` bridge.

Builds an ``ActionAgent`` in each of its three branches (bang shell,
literal slash, natural-language LLM) and asserts the projected hooks
return exactly what the subclass methods return.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from core.agent_harness.action_agent import ActionAgent
from core.agent_harness.action_hooks import action_agent_to_hooks
from core.agent_harness.surface_hooks import SurfaceHooks


class _FakeAgentTool:
    """Minimal ``AgentTool`` — just carries a ``name`` for slash-command matching."""

    def __init__(self, name: str) -> None:
        self.name = name

    def __repr__(self) -> str:
        return f"_FakeAgentTool({self.name!r})"


@dataclass
class _RecordingToolProvider:
    """In-memory ``ToolProvider`` for tests."""

    tools: list[Any] = field(default_factory=list)

    def action_tools(self, *, confirm_fn: Any, is_tty: bool | None) -> list[Any]:
        _ = (confirm_fn, is_tty)
        return list(self.tools)

    def tool_resources(self) -> dict[str, Any]:
        return {}

    def observer(self, *, message: str) -> Any:
        _ = message
        return lambda _kind, _data: None


class _StubSessionStore:
    """Minimal session — only touched fields matter for prompt construction."""

    def __init__(self) -> None:
        self.cli_agent_messages: list[tuple[str, str]] = []
        self.configured_integrations_known: bool = True
        self.configured_integrations: tuple[str, ...] = ()
        self.last_state: dict[str, Any] | None = None
        self.last_synthetic_observation_path: str | None = None
        self.reasoning_effort: Any | None = None
        self.history: list[dict[str, Any]] = []
        self.last_command_observation: str | None = None
        self.session_id: str = "test-session"
        self.resolved_integrations_cache: dict[str, Any] | None = None
        self.github_repo_scope: tuple[str, str] | None = None


def _make_action_agent(message: str, *, tools: list[Any] | None = None) -> ActionAgent:
    return ActionAgent(
        session=_StubSessionStore(),  # type: ignore[arg-type]
        tools_provider=_RecordingToolProvider(tools=tools or []),
        message=message,
    )


# ---- Bundle shape ----


def test_bundle_satisfies_the_surface_hooks_protocol() -> None:
    agent = _make_action_agent("hello world")
    hooks = action_agent_to_hooks(agent)
    assert isinstance(hooks, SurfaceHooks)


# ---- Bang shell branch ----


def test_bang_command_branch_projects_the_same_tools() -> None:
    """The bundle returns the tools ``ActionAgent`` captured at construction."""
    slash_tool = _FakeAgentTool("slash_invoke")
    other_tool = _FakeAgentTool("shell_run")
    agent = _make_action_agent("!ls -la", tools=[slash_tool, other_tool])

    hooks = action_agent_to_hooks(agent)
    tools_via_hook = hooks.resolve_tools(None)  # type: ignore[arg-type]

    assert [t.name for t in tools_via_hook] == ["slash_invoke", "shell_run"]


def test_bang_command_branch_projects_the_shell_run_prompt() -> None:
    agent = _make_action_agent("!ls -la")
    hooks = action_agent_to_hooks(agent)
    assert hooks.construct_prompt(None) == agent.build_system_prompt()  # type: ignore[arg-type]
    assert hooks.construct_prompt(None) == "Execute the explicit shell_run tool call."  # type: ignore[arg-type]


# ---- Literal slash branch ----


def test_slash_command_branch_projects_the_slash_invoke_prompt() -> None:
    """A bare `/command` yields the slash-specific prompt via the hook."""
    slash_tool = _FakeAgentTool("slash_invoke")
    agent = _make_action_agent("/help", tools=[slash_tool])

    hooks = action_agent_to_hooks(agent)

    assert hooks.construct_prompt(None) == agent.build_system_prompt()  # type: ignore[arg-type]
    assert hooks.construct_prompt(None) == "Execute the explicit slash_invoke tool call."  # type: ignore[arg-type]


# ---- Natural-language / LLM branch ----


def test_llm_branch_projects_the_action_system_prompt() -> None:
    """Natural-language messages take the LLM branch; the hook returns the
    same string as ``ActionAgent.build_system_prompt``."""
    agent = _make_action_agent("please look at the recent errors")
    hooks = action_agent_to_hooks(agent)

    projected = hooks.construct_prompt(None)  # type: ignore[arg-type]
    reference = agent.build_system_prompt()

    assert projected == reference
    assert projected not in {
        "Execute the explicit shell_run tool call.",
        "Execute the explicit slash_invoke tool call.",
    }


# ---- No-op hooks ----


def test_inject_context_is_the_identity_projection() -> None:
    """The bundle's ``inject_context`` returns the input request unchanged."""
    agent = _make_action_agent("hello")
    hooks = action_agent_to_hooks(agent)

    request = {"messages": [], "system": "s", "tools": [], "metadata": {"iteration": 0}}
    assert hooks.inject_context(None, request) is request  # type: ignore[arg-type]


def test_route_response_is_a_noop() -> None:
    """``run_action_agent_turn`` handles output at the wrapper level."""
    agent = _make_action_agent("hello")
    hooks = action_agent_to_hooks(agent)
    assert hooks.route_response(None, "assistant reply") is None  # type: ignore[arg-type]


# ---- Repeated calls ----


def test_resolve_tools_returns_a_fresh_list_on_each_call() -> None:
    """Callers mutating the returned list must not affect the agent."""
    tool = _FakeAgentTool("shell_run")
    agent = _make_action_agent("!ls", tools=[tool])
    hooks = action_agent_to_hooks(agent)

    first = hooks.resolve_tools(None)  # type: ignore[arg-type]
    second = hooks.resolve_tools(None)  # type: ignore[arg-type]

    assert first == second
    assert first is not second


def test_construct_prompt_delegates_live_to_action_agent() -> None:
    """Repeated calls consult ``ActionAgent`` each time (no cached freeze)."""
    agent = _make_action_agent("hello")
    hooks = action_agent_to_hooks(agent)
    assert hooks.construct_prompt(None) == agent.build_system_prompt()  # type: ignore[arg-type]
    # Call again; must still equal the live agent method.
    assert hooks.construct_prompt(None) == agent.build_system_prompt()  # type: ignore[arg-type]
