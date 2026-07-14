"""Tests for gateway turn handler wiring."""

from __future__ import annotations

import logging
from typing import Any
from unittest.mock import MagicMock

from rich.console import Console

from core.agent_harness.session import SessionCore
from core.agent_harness.session.persistence.memory import InMemorySessionStorage
from core.agent_harness.turns.turn_results import ShellTurnResult, ToolCallingTurnResult
from gateway.runtime.turn_handler import GatewayTurnHandler


def _patch_headless_agent(monkeypatch: Any, result: ShellTurnResult) -> MagicMock:
    """Patch the gateway's ``HeadlessAgent`` so construction is inert and dispatch returns ``result``.

    Returns the patched class mock; ``mock.call_args.kwargs`` exposes the constructor
    ports (e.g. ``tools``) the gateway wired for the turn.
    """
    agent_cls = MagicMock()
    agent_cls.return_value.dispatch.return_value = result
    monkeypatch.setattr("gateway.runtime.turn_handler.HeadlessAgent", agent_cls)
    return agent_cls


def test_turn_handler_resolves_action_tools_from_live_session(monkeypatch: Any) -> None:
    """Per-chat session integrations must drive the action tool list each turn.

    Precomputing tools at gateway boot (from an empty boot session) left the
    action agent with no integration-scoped tools, so ``run_turn`` fell through
    to the answer CLI agent on Telegram while the shell worked.
    """
    recorded: list[dict[str, Any] | None] = []

    def _fake_get_tools(
        _ctx: Any,
        *,
        resolved_integrations: dict[str, Any] | None = None,
    ) -> list[Any]:
        recorded.append(resolved_integrations)
        return [MagicMock(name="slack_send_message")]

    monkeypatch.setattr(
        "core.agent_harness.tools.tool_provider.get_action_tools_from_integrations_context",
        _fake_get_tools,
    )

    agent_cls = _patch_headless_agent(
        monkeypatch,
        ShellTurnResult(
            final_intent="cli_agent_handled",
            action_result=ToolCallingTurnResult(
                planned_count=1,
                executed_count=1,
                executed_success_count=1,
                has_unhandled_clause=False,
                handled=True,
            ),
        ),
    )

    session = SessionCore(storage=InMemorySessionStorage())
    chat_integrations = {"slack": {"webhook_url": "https://hooks.example/test"}}
    session.resolved_integrations_cache = chat_integrations

    handler = GatewayTurnHandler(console=Console(force_terminal=False))
    handler("send slack update", session, MagicMock(), logging.getLogger("test.turn_handler"))

    tool_provider = agent_cls.call_args.kwargs["tools"]
    tools = tool_provider.action_tools(confirm_fn=None, is_tty=False)
    assert len(tools) == 1
    assert recorded == [chat_integrations]


def _empty_turn_result(*, llm_run: Any = None) -> ShellTurnResult:
    return ShellTurnResult(
        final_intent="cli_agent_handled",
        action_result=ToolCallingTurnResult(
            planned_count=0,
            executed_count=0,
            executed_success_count=0,
            has_unhandled_clause=False,
            handled=True,
            response_text="",
        ),
        assistant_response_text="",
        llm_run=llm_run,
    )


def test_turn_handler_finalizes_fallback_on_empty_response(monkeypatch: Any) -> None:
    """An empty, non-answered turn still finalizes so the placeholder status can't hang."""
    _patch_headless_agent(monkeypatch, _empty_turn_result())
    sink = MagicMock()
    handler = GatewayTurnHandler(console=Console(force_terminal=False))
    handler("/", SessionCore(storage=InMemorySessionStorage()), sink, logging.getLogger("test"))
    sink.finalize.assert_called_once_with("I didn't have anything to add for that.")


def test_turn_handler_skips_finalize_when_answer_was_streamed(monkeypatch: Any) -> None:
    """A streamed answer (llm_run set) already resolved the status; do not re-finalize."""
    result = _empty_turn_result(llm_run=MagicMock())  # answered=True
    _patch_headless_agent(monkeypatch, result)
    sink = MagicMock()
    handler = GatewayTurnHandler(console=Console(force_terminal=False))
    handler("hi", SessionCore(storage=InMemorySessionStorage()), sink, logging.getLogger("test"))
    sink.finalize.assert_not_called()
