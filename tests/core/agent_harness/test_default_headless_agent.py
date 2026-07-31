"""Characterization for the shared default HeadlessAgent factory."""

from __future__ import annotations

from io import StringIO
from types import SimpleNamespace

from rich.console import Console

from core.agent_harness.turns.default_headless_agent import build_default_headless_agent
from core.agent_harness.turns.headless_adapters import BufferOutputSink
from core.agent_harness.turns.turn_results import ShellTurnResult, ToolCallingTurnResult


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


def test_primary_response_text_prefers_assistant() -> None:
    result = ShellTurnResult(
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
    empty_assistant = ShellTurnResult(
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
