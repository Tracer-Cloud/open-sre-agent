from __future__ import annotations

import logging
from unittest.mock import MagicMock, patch

from core.agent_harness.session.integrations_cache import (
    has_only_runtime_metadata,
    has_resolved_integrations,
    merge_resolved_integrations,
)
from core.agent_harness.turn_results import ShellTurnResult, ToolCallingTurnResult
from gateway.approvals.telegram import TelegramApprovalService
from gateway.core.dispatch_gateway_msg_to_agent import dispatch_gateway_msg_to_agent


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


@patch("gateway.core.error_handling.report_exception")
@patch("gateway.core.dispatch_gateway_msg_to_agent.dispatch_message_to_headless_agent")
def test_dispatch_gateway_msg_to_agent_reports_exception_and_renders_error(
    mock_turn: MagicMock,
    mock_report: MagicMock,
) -> None:
    mock_turn.side_effect = RuntimeError("boom")
    session = MagicMock()
    session.session_id = "session-1"
    sink = MagicMock()
    approval = MagicMock(spec=TelegramApprovalService)
    test_logger = logging.getLogger("gateway.tests")

    result = dispatch_gateway_msg_to_agent(
        text="hi",
        session=session,
        chat_id="42",
        sink=sink,
        approval_service=approval,
        logger=test_logger,
    )

    mock_report.assert_called_once()
    assert mock_report.call_args.kwargs["tags"]["gateway.turn_outcome"] == "exception"
    sink.render_error.assert_called_once()
    assert result.final_intent == "gateway_error"


@patch("gateway.core.error_handling.report_exception")
@patch("gateway.core.dispatch_gateway_msg_to_agent.dispatch_message_to_headless_agent")
def test_dispatch_gateway_msg_to_agent_reports_empty_response(
    mock_turn: MagicMock,
    mock_report: MagicMock,
) -> None:
    mock_turn.return_value = ShellTurnResult(
        final_intent="gather_and_answer",
        action_result=ToolCallingTurnResult(0, 0, 0, False, False),
    )
    session = MagicMock()
    session.session_id = "session-1"
    sink = MagicMock()
    approval = MagicMock(spec=TelegramApprovalService)
    test_logger = logging.getLogger("gateway.tests")

    result = dispatch_gateway_msg_to_agent(
        text="hi",
        session=session,
        chat_id="42",
        sink=sink,
        approval_service=approval,
        logger=test_logger,
    )

    mock_report.assert_called_once()
    assert mock_report.call_args.kwargs["tags"]["gateway.turn_outcome"] == "empty_response"
    sink.render_error.assert_called_once()
    assert result.final_intent == "gather_and_answer"


@patch("gateway.core.dispatch_gateway_msg_to_agent.dispatch_message_to_headless_agent")
def test_dispatch_gateway_msg_to_agent_passes_sink_and_hooks(mock_turn: MagicMock) -> None:
    mock_turn.return_value = ShellTurnResult(
        final_intent="gather_and_answer",
        action_result=ToolCallingTurnResult(0, 0, 0, False, False),
        assistant_response_text="hello",
        llm_run=MagicMock(response_text="hello"),
    )
    session = MagicMock()
    sink = MagicMock()
    approval = MagicMock(spec=TelegramApprovalService)
    approval.hooks.return_value = MagicMock()
    approval.wait_for_confirmation.return_value = "yes"

    dispatch_gateway_msg_to_agent(
        text="hi",
        session=session,
        chat_id="42",
        sink=sink,
        approval_service=approval,
        logger=logging.getLogger("gateway.tests"),
    )

    session.warm_resolved_integrations.assert_not_called()
    kwargs = mock_turn.call_args.kwargs
    assert mock_turn.call_args.args == ("hi",)
    assert kwargs["session"] is session
    assert kwargs["is_tty"] is False
    assert kwargs["output"] is sink
    assert kwargs["gather_enabled"] is True
    assert kwargs["tool_hooks"] is approval.hooks.return_value


@patch("gateway.core.dispatch_gateway_msg_to_agent.dispatch_message_to_headless_agent")
def test_dispatch_gateway_msg_to_agent_finalizes_unanswered_action_response(mock_turn: MagicMock) -> None:
    mock_turn.return_value = ShellTurnResult(
        final_intent="cli_agent_handled",
        action_result=ToolCallingTurnResult(
            1,
            1,
            1,
            False,
            True,
            response_text="OpenSRE Health",
        ),
    )
    session = MagicMock()
    sink = MagicMock()
    approval = MagicMock(spec=TelegramApprovalService)
    approval.hooks.return_value = MagicMock()

    dispatch_gateway_msg_to_agent(
        text="/health",
        session=session,
        chat_id="42",
        sink=sink,
        approval_service=approval,
        logger=logging.getLogger("gateway.tests"),
    )

    sink.finalize.assert_called_once_with("OpenSRE Health")
