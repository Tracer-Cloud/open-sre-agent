from __future__ import annotations

from unittest.mock import MagicMock, patch

from core.agent_harness.session.integrations_cache import (
    has_only_runtime_metadata,
    has_resolved_integrations,
    merge_resolved_integrations,
)
from core.agent_harness.turn_results import ShellTurnResult, ToolCallingTurnResult
from gateway.config import GatewaySettings
from gateway.turn_executor import execute_gateway_turn


def test_has_resolved_integrations_ignores_gateway_metadata() -> None:
    assert has_resolved_integrations({"_gateway_chat_id": "1"}) is False
    assert has_resolved_integrations({"github": {"token": "x"}}) is True


def test_has_only_runtime_metadata_distinguishes_empty_cache() -> None:
    assert has_only_runtime_metadata({"_gateway_chat_id": "1"}) is True
    assert has_only_runtime_metadata({}) is False
    assert has_only_runtime_metadata({"github": {"token": "x"}}) is False


def test_merge_resolved_integrations_preserves_gateway_metadata() -> None:
    merged = merge_resolved_integrations(
        {"_gateway_chat_id": "42"},
        {"github": {"token": "x"}},
    )
    assert merged["_gateway_chat_id"] == "42"
    assert merged["github"]["token"] == "x"


@patch("gateway.turn_executor.execute_shell_turn")
def test_execute_gateway_turn_passes_sink_and_hooks(mock_turn: MagicMock) -> None:
    mock_turn.return_value = ShellTurnResult(
        final_intent="gather_and_answer",
        action_result=ToolCallingTurnResult(0, 0, 0, False, False),
        assistant_response_text="hello",
        llm_run=MagicMock(response_text="hello"),
    )
    session = MagicMock()
    session.resolved_integrations_cache = None
    client = MagicMock()
    client.send_message.return_value = (True, "", "1")
    client.send_chat_action.return_value = None
    approval = MagicMock()
    approval.hooks.return_value = MagicMock()
    approval.wait_for_confirmation.return_value = "yes"

    execute_gateway_turn(
        text="hi",
        session=session,
        client=client,
        chat_id="42",
        settings=GatewaySettings(),
        approval_service=approval,
    )

    session.warm_resolved_integrations.assert_called_once()
    kwargs = mock_turn.call_args.kwargs
    assert kwargs["is_tty"] is False
    assert kwargs["output"] is not None
    assert kwargs["tool_hooks"] is approval.hooks.return_value


@patch("gateway.turn_executor.execute_shell_turn")
def test_execute_gateway_turn_warms_before_gateway_context(mock_turn: MagicMock) -> None:
    mock_turn.return_value = ShellTurnResult(
        final_intent="gather_and_answer",
        action_result=ToolCallingTurnResult(0, 0, 0, False, False),
        assistant_response_text="42 stars",
        llm_run=MagicMock(response_text="42 stars"),
    )
    session = MagicMock()
    session.resolved_integrations_cache = None

    def _warm() -> None:
        session.resolved_integrations_cache = {"github": {"token": "x"}}

    session.warm_resolved_integrations.side_effect = _warm
    client = MagicMock()
    client.send_message.return_value = (True, "", "1")
    client.send_chat_action.return_value = None
    approval = MagicMock()
    approval.hooks.return_value = MagicMock()

    execute_gateway_turn(
        text="how many github stars?",
        session=session,
        client=client,
        chat_id="99",
        settings=GatewaySettings(),
        approval_service=approval,
    )

    assert session.resolved_integrations_cache["github"] == {"token": "x"}
    assert session.resolved_integrations_cache["_gateway_chat_id"] == "99"


@patch("gateway.turn_executor.execute_shell_turn")
def test_execute_gateway_turn_finalizes_captured_console_output(mock_turn: MagicMock) -> None:
    def _run_turn(
        _text: str,
        _session: object,
        console: object,
        **kwargs: object,
    ) -> ShellTurnResult:
        console.print("OpenSRE Health")  # type: ignore[attr-defined]
        return ShellTurnResult(
            final_intent="cli_agent_handled",
            action_result=ToolCallingTurnResult(1, 1, 1, False, True),
        )

    mock_turn.side_effect = _run_turn
    session = MagicMock()
    session.resolved_integrations_cache = None
    client = MagicMock()
    client.send_message.return_value = (True, "", "1")
    client.edit_message_text.return_value = (True, "")
    client.send_chat_action.return_value = None
    approval = MagicMock()
    approval.hooks.return_value = MagicMock()

    execute_gateway_turn(
        text="/health",
        session=session,
        client=client,
        chat_id="42",
        settings=GatewaySettings(),
        approval_service=approval,
    )

    client.edit_message_text.assert_called()
    final_text = client.edit_message_text.call_args.args[2]
    assert "OpenSRE Health" in final_text
