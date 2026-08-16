"""Characterization for the shared default HeadlessAgent factory."""

from __future__ import annotations

from io import StringIO
from types import SimpleNamespace

from rich.console import Console

from core.agent_harness.session import SessionCore
from core.agent_harness.session.persistence.memory import InMemorySessionStore
from core.agent_harness.turns.default_headless_agent import build_default_headless_agent
from core.agent_harness.turns.headless_adapters import BufferOutputSink
from core.agent_harness.turns.turn_results import ToolCallingTurnResult, TurnResult
from core.execution import ToolExecutionHooks


def test_build_default_headless_agent_sets_gateway_surface() -> None:
    session = SimpleNamespace(
        configured_integrations=[],
        resolved_integrations_cache={},
        session_id="s1",
    )
    agent = build_default_headless_agent(
        session=session,
        output=BufferOutputSink(),
        console=Console(force_terminal=False, file=StringIO()),
        logger=__import__("logging").getLogger("test"),
        surface="gateway",
    )
    prompts = agent._prompts
    assert prompts.surface() == "gateway"


def test_factory_is_exported_from_runtime_and_the_buffer_sink_is_not() -> None:
    import core.agent_harness as pkg
    from core.agent_harness import runtime

    assert runtime.build_default_headless_agent is build_default_headless_agent
    assert not hasattr(pkg, "build_default_headless_agent")
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
    agent = build_default_headless_agent(
        session=session,
        output=BufferOutputSink(),
        prompts=supplied,  # type: ignore[arg-type]
    )

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
    agent = build_default_headless_agent(session=session, output=BufferOutputSink())

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


def test_factory_forwards_every_host_port_to_the_tool_provider_and_runner() -> None:
    """A host with shell-shaped needs can build its agent through the factory.

    ``DefaultToolProvider`` already accepted these ports; the factory now
    forwards them, so a host no longer has to assemble the provider and the
    action runner by hand to pass ``request_exit`` or its own investigation /
    LLM-provider / task-cancel / slash port factories.
    """
    from core.agent_harness.turns.action_driver import ToolCallingDeps

    seen: dict[str, object] = {}

    def _investigation_ports() -> object:
        seen["investigation"] = True
        return object()

    def _llm_provider_ports() -> object:
        seen["llm_provider"] = True
        return object()

    def _task_cancel_ports() -> object:
        seen["task_cancel"] = True
        return object()

    def _slash_ports() -> object:
        seen["slash"] = True
        return object()

    def _request_exit() -> None:
        seen["exit"] = True

    deps = ToolCallingDeps(llm_factory=lambda *_a, **_k: None)
    hooks = ToolExecutionHooks()

    # Act
    agent = build_default_headless_agent(
        session=SessionCore(store=InMemorySessionStore()),
        output=BufferOutputSink(),
        request_exit=_request_exit,
        investigation_ports_factory=_investigation_ports,
        llm_provider_ports_factory=_llm_provider_ports,
        task_cancel_ports_factory=_task_cancel_ports,
        slash_ports_factory=_slash_ports,
        deps=deps,
        tool_hooks=hooks,
        confirm_fn=lambda _prompt: "y",
    )

    # Assert — the provider holds the host's port factories and exit hook; the
    # runner holds the host's deps and hooks
    provider = agent._tools  # noqa: SLF001
    assert provider._request_exit is _request_exit  # noqa: SLF001
    assert provider._investigation_ports_factory is _investigation_ports  # noqa: SLF001
    assert provider._llm_provider_ports_factory is _llm_provider_ports  # noqa: SLF001
    assert provider._task_cancel_ports_factory is _task_cancel_ports  # noqa: SLF001
    assert provider._slash_ports_factory is _slash_ports  # noqa: SLF001
    runner = agent._action_runner  # noqa: SLF001
    assert runner.deps is deps
    assert runner.tool_hooks is hooks
    assert agent._confirm_fn is not None  # noqa: SLF001
