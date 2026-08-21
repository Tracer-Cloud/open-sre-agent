"""Characterization for ``DefaultHeadlessBuild`` — the default port family and the agent built on it."""

from __future__ import annotations

from io import StringIO
from types import SimpleNamespace

from rich.console import Console

from core.agent_harness.runtime import TurnBinding
from core.agent_harness.session import SessionCore
from core.agent_harness.session.persistence.memory import InMemorySessionStore
from core.agent_harness.turns.headless_adapters import BufferOutputSink
from core.agent_harness.turns.headless_build import DefaultHeadlessBuild
from core.agent_harness.turns.turn_results import ToolCallingTurnResult, TurnResult
from core.tool.execution import ToolExecutionHooks


def test_default_headless_build_sets_gateway_surface() -> None:
    session = SimpleNamespace(
        configured_integrations=[],
        resolved_integrations_cache={},
        session_id="s1",
    )
    agent = DefaultHeadlessBuild(
        session=session,
        output=BufferOutputSink(),
        console=Console(force_terminal=False, file=StringIO()),
        logger=__import__("logging").getLogger("test"),
        surface="gateway",
    ).agent()
    prompts = agent._prompts
    assert prompts.surface() == "gateway"


def test_default_headless_build_is_exported_from_runtime_and_the_buffer_sink_is_not() -> None:
    import core.agent_harness as pkg
    import core.agent_harness.runtime as runtime

    assert runtime.DefaultHeadlessBuild is DefaultHeadlessBuild
    assert not hasattr(pkg, "DefaultHeadlessBuild")
    assert not hasattr(pkg, "BufferOutputSink")
    assert not hasattr(runtime, "BufferOutputSink")


def test_builder_uses_supplied_prompts_even_when_falsy() -> None:
    """``prompts=`` selection is ``is not None``, matching ``HeadlessAgent``."""
    # Arrange
    session = SimpleNamespace(
        configured_integrations=[],
        resolved_integrations_cache={},
        session_id="s1",
    )

    class _FalsyPrompts:
        def __bool__(self) -> bool:
            return False

    supplied = _FalsyPrompts()

    # Act
    agent = DefaultHeadlessBuild(session=session, output=BufferOutputSink()).agent(prompts=supplied)

    # Assert
    assert agent._prompts is supplied  # noqa: SLF001


def test_builder_defaults_prompts_when_omitted() -> None:
    """No ``prompts=`` keeps the built-in grounding provider."""
    # Arrange
    session = SimpleNamespace(
        configured_integrations=[],
        resolved_integrations_cache={},
        session_id="s1",
    )

    # Act
    agent = DefaultHeadlessBuild(session=session, output=BufferOutputSink()).agent()

    # Assert
    assert type(agent._prompts).__name__ == "DefaultPromptContextProvider"  # noqa: SLF001


def test_primary_response_text_prefers_assistant() -> None:
    result = TurnResult(
        final_intent="ok",
        action_result=ToolCallingTurnResult(
            planned_count=0,
            executed_count=0,
            executed_success_count=0,
            has_unhandled_clause=False,
            handled=True,
            response_text="from action",
        ),
        assistant_response_text=" from assistant ",
        llm_run=object(),
    )
    assert result.primary_response_text == "from assistant"
    empty_assistant = TurnResult(
        final_intent="ok",
        action_result=ToolCallingTurnResult(
            planned_count=0,
            executed_count=0,
            executed_success_count=0,
            has_unhandled_clause=False,
            handled=True,
            response_text=" from action ",
        ),
        assistant_response_text="",
        llm_run=object(),
    )
    assert empty_assistant.primary_response_text == "from action"


def test_default_headless_build_takes_the_hosts_tool_provider_and_forwards_the_llm_factory() -> (
    None
):
    """A host varies the agent through its ``ToolProvider``; ``llm_factory`` reaches the runner.

    ``tools`` is the bridge between the agent and a host's tool stack: the shell
    and gateway each configure a ``DefaultToolProvider`` and pass it in. Absent,
    the family's bare default provider is used so a script can dispatch with
    zero configuration.
    """
    from core.agent_harness.tools.tool_provider import DefaultToolProvider

    # Arrange — a host-configured tool provider, an action LLM factory, and hooks.
    session = SessionCore(store=InMemorySessionStore())
    tools = DefaultToolProvider(session, Console(file=StringIO()))

    def llm_factory() -> object:
        return object()

    hooks = ToolExecutionHooks()

    # Act
    agent = DefaultHeadlessBuild(session=session, output=BufferOutputSink()).agent(
        tools=tools, llm_factory=llm_factory
    )
    agent.bind_turn(TurnBinding(tool_hooks=hooks))
    bare = DefaultHeadlessBuild(session=session, output=BufferOutputSink()).agent()

    # Assert — the host's provider is used as-is; the factory and hooks reach the
    # runner; no ``tools`` yields a default provider.
    assert agent._tools is tools  # noqa: SLF001
    runner = agent._action_runner  # noqa: SLF001
    assert runner.llm_factory is llm_factory
    assert runner.tool_hooks is hooks
    assert isinstance(bare._tools, DefaultToolProvider)  # noqa: SLF001


def test_a_stage_override_replaces_only_that_stage() -> None:
    """A host or test may swap one stage; the other two keep the port-driven default.

    This is the seam every turn test drives (98 sites inject a single stage);
    it is part of the host contract, so it lives on the agent explicitly.
    """
    calls: list[str] = []

    def _fake_execute(text: str, *, confirm_fn=None, is_tty=None, turn_plan=None):  # type: ignore[no-untyped-def]
        calls.append(f"execute:{text}")
        return ToolCallingTurnResult(
            planned_count=0,
            executed_count=0,
            executed_success_count=0,
            has_unhandled_clause=False,
            handled=True,
        )

    # Act — only execute_actions is overridden, on the built agent
    agent = DefaultHeadlessBuild(
        session=SessionCore(store=InMemorySessionStore()), output=BufferOutputSink()
    ).agent()
    agent.bind_stages(execute_actions=_fake_execute)
    result = agent.dispatch("hello")

    # Assert — the override ran and handled the turn; answer/gather defaults untouched
    assert calls == ["execute:hello"]
    assert isinstance(result, TurnResult)
    assert agent._answer_override is None  # noqa: SLF001
    assert agent._gather_override is None  # noqa: SLF001
