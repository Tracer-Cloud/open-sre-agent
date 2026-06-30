"""Execute one gateway turn through the headless agent harness."""

from __future__ import annotations

import logging
from typing import Any

from core.agent_harness.headless import StaticReasoningClientProvider
from core.agent_harness.headless_agent import dispatch_message_to_headless_agent
from core.agent_harness.session import ReplSession
from core.agent_harness.turn_results import ShellTurnResult
from gateway.agent.error_handling import (
    EMPTY_RESPONSE_MESSAGE,
    USER_ERROR_MESSAGE,
    failed_turn_result,
    reply_text_for_unanswered_turn,
    report_turn_failure,
)
from gateway.agent.gateway_agent_adapters import (
    GatewayErrorReporter,
    GatewayPromptContextProvider,
    GatewayRunRecordFactory,
    GatewayToolProvider,
)
from gateway.agent.gateway_output_sink import GatewayOutputSink
from gateway.session.gateway_chat_context import inject_gateway_chat_context


def _gateway_reasoning_provider(logger: logging.Logger) -> StaticReasoningClientProvider:
    """Resolve the configured reasoning client for non-interactive gateway turns."""
    try:
        from core.llm.llm_client import get_llm_for_reasoning
    except Exception as exc:
        logger.exception("[gateway] reasoning client unavailable: %s", exc)
        return StaticReasoningClientProvider()

    client: Any | None = get_llm_for_reasoning()
    return StaticReasoningClientProvider(client=client)


def dispatch_gateway_msg_to_agent(
    *,
    text: str,
    session: ReplSession,
    chat_id: str,
    sink: GatewayOutputSink,
    logger: logging.Logger,
) -> ShellTurnResult:
    """Run a full gateway turn and stream the answer through the provided sink."""

    session.resolved_integrations_cache = inject_gateway_chat_context(
        dict(session.resolved_integrations_cache or {}),
        chat_id,
    )

    try:
        result: ShellTurnResult = dispatch_message_to_headless_agent(
            text,
            session=session,
            is_tty=False,
            output=sink,
            prompts=GatewayPromptContextProvider(session),
            reasoning=_gateway_reasoning_provider(logger),
            tools=GatewayToolProvider(
                session=session,
                sink=sink,
                chat_id=chat_id,
                logger=logger,
            ),
            run_factory=GatewayRunRecordFactory(session),
            error_reporter=GatewayErrorReporter(logger),
            gather_enabled=True,
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
        "turn complete chat=%s answered=%s intent=%s",
        chat_id,
        result.answered,
        result.final_intent,
    )
    return result
