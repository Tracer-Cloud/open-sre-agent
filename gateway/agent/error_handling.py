"""Gateway turn error reporting and degraded result helpers."""

from __future__ import annotations

import logging
from typing import Any

from core.agent_harness.session import ReplSession
from core.agent_harness.turn_results import ShellTurnResult, ToolCallingTurnResult
from platform.observability.errors import report_exception

USER_ERROR_MESSAGE = "Something went wrong while processing your message. Please try again."
EMPTY_RESPONSE_MESSAGE = "I couldn't produce a response. Please try again."
TEXT_PREVIEW_LIMIT = 200


class GatewayAgentTurnError(Exception):
    """Synthetic error used to report failed gateway turns to Sentry."""


def turn_tags(*, outcome: str) -> dict[str, str]:
    return {
        "surface": "gateway",
        "component": "gateway.agent.error_handling",
        "gateway.turn_outcome": outcome,
    }


def turn_extras(
    *,
    chat_id: str,
    session: ReplSession,
    text: str,
    result: ShellTurnResult | None = None,
) -> dict[str, Any]:
    extras: dict[str, Any] = {
        "chat_id": chat_id,
        "session_id": session.session_id,
        "text_length": len(text),
        "text_preview": text[:TEXT_PREVIEW_LIMIT],
    }
    if result is not None:
        extras.update(
            {
                "final_intent": result.final_intent,
                "answered": result.answered,
                "action_handled": result.action_result.handled,
                "action_planned_count": result.action_result.planned_count,
                "action_executed_count": result.action_result.executed_count,
                "action_response_length": len(result.action_result.response_text),
                "assistant_response_length": len(result.assistant_response_text),
                "action_accounting_status": result.action_result.accounting_status,
            }
        )
    return extras


def failed_turn_result() -> ShellTurnResult:
    return ShellTurnResult(
        final_intent="gateway_error",
        action_result=ToolCallingTurnResult(0, 0, 0, False, False, accounting_status="not_run"),
    )


def reply_text_for_unanswered_turn(result: ShellTurnResult) -> str:
    return result.assistant_response_text.strip() or result.action_result.response_text.strip()


def report_turn_failure(
    *,
    logger: logging.Logger,
    outcome: str,
    message: str,
    chat_id: str,
    session: ReplSession,
    text: str,
    result: ShellTurnResult | None = None,
    exc: BaseException | None = None,
) -> None:
    error = exc or GatewayAgentTurnError(message)
    report_exception(
        error,
        logger=logger,
        message=message,
        tags=turn_tags(outcome=outcome),
        extras=turn_extras(chat_id=chat_id, session=session, text=text, result=result),
    )
