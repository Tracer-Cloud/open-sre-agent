"""Execute one gateway turn through the headless agent harness."""

from __future__ import annotations

import logging

from core.agent_harness.headless_agent import dispatch_message_to_headless_agent
from core.agent_harness.session import ReplSession
from core.agent_harness.turn_results import ShellTurnResult
from core.execution import ToolExecutionHooks
from gateway.approvals.telegram import TelegramApprovalService
from gateway.core.error_handling import (
    EMPTY_RESPONSE_MESSAGE,
    USER_ERROR_MESSAGE,
    failed_turn_result,
    reply_text_for_unanswered_turn,
    report_turn_failure,
)
from gateway.core.gateway_output_sink import GatewayOutputSink


def dispatch_gateway_msg_to_agent(
    *,
    text: str,
    session: ReplSession,
    chat_id: str,
    sink: GatewayOutputSink,
    approval_service: TelegramApprovalService,
    logger: logging.Logger,
) -> ShellTurnResult:
    """Run a full gateway turn and stream the answer through the provided sink."""

    hooks: ToolExecutionHooks = approval_service.hooks()

    try:
        result: ShellTurnResult = dispatch_message_to_headless_agent(
            text,
            session=session,
            confirm_fn=lambda p: approval_service.wait_for_confirmation(chat_id=chat_id, prompt=p),
            is_tty=False,
            output=sink,
            gather_enabled=True,
            tool_hooks=hooks,
        )
    except Exception as exc:
        report_turn_failure(
            logger=logger,
            outcome="exception",
            message="[gateway] agent turn raised",
            chat_id=chat_id,
            session=session,
            text=text,
            exc=exc,
        )
        sink.render_error(USER_ERROR_MESSAGE)
        return failed_turn_result()

    # Finalize response for unanswered turns
    if not result.answered:
        try:
            reply_text = reply_text_for_unanswered_turn(result)
            if reply_text:
                sink.finalize(reply_text)
            else:
                report_turn_failure(
                    logger=logger,
                    outcome="empty_response",
                    message="[gateway] agent turn produced no user-visible response",
                    chat_id=chat_id,
                    session=session,
                    text=text,
                    result=result,
                )
                sink.render_error(EMPTY_RESPONSE_MESSAGE)
        except Exception as exc:
            report_turn_failure(
                logger=logger,
                outcome="finalize_failed",
                message="[gateway] failed to deliver agent response",
                chat_id=chat_id,
                session=session,
                text=text,
                result=result,
                exc=exc,
            )
            sink.render_error(USER_ERROR_MESSAGE)
            return failed_turn_result()

    logger.info(
        "[gateway] turn complete answered=%s intent=%s",
        result.answered, result.final_intent
    )
    return result
