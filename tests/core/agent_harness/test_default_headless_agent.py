"""Characterization for the shared default HeadlessAgent factory."""

from __future__ import annotations

from io import StringIO
from types import SimpleNamespace

from rich.console import Console

from core.agent_harness.turns.default_headless_agent import build_default_headless_agent
from core.agent_harness.turns.headless_adapters import BufferOutputSink
from core.agent_harness.turns.turn_results import ToolCallingTurnResult, TurnResult


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
