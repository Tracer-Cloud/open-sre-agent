"""Inline-keyboard approval prompt for write tools on Telegram.

The transport-neutral half — broker, button identifiers, harness hooks — lives
in :mod:`gateway.core.runtime.approvals`. This module renders the Telegram side:
an Approve / Deny inline keyboard, and callback_query routing back to the broker.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from typing import Any

from gateway.core.runtime.approvals import (
    APPROVE_ACTION_ID,
    DENY_ACTION_ID,
    MAX_APPROVAL_WAIT_SECONDS,
    ApprovalBroker,
    DecidedPrompts,
)
from gateway.transports.telegram.poller.client import TelegramBotClient
from gateway.transports.telegram.settings import TelegramCallbackQuery

logger = logging.getLogger("gateway")


class TelegramApprovalPrompter:
    """Posts Approve/Deny buttons in-chat and waits for an authorized click."""

    def __init__(
        self,
        *,
        client: TelegramBotClient,
        broker: ApprovalBroker,
        chat_id: str,
    ) -> None:
        self._client = client
        self._broker = broker
        self._chat_id = chat_id
        self._decided = DecidedPrompts()

    def request(
        self,
        *,
        call_id: str,
        headline: str,
        reason: str,
        details: str,
        expiry_seconds: float,
    ) -> tuple[bool, str]:
        approval_id = self._broker.create(
            platform="telegram",
            chat_id=self._chat_id,
        )
        body = f"🔒 Approval needed — {headline}"
        if reason.strip():
            body += f"\n{reason.strip()}"
        if details.strip():
            body += f"\n```\n{details.strip()}\n```"
        ok, error, message_id = self._client.send_message(
            self._chat_id,
            body,
            reply_markup=_approval_keyboard(approval_id),
        )
        if not ok or not message_id:
            logger.warning(
                "[telegram-gateway] approval prompt post failed headline=%s chat=%s err=%s",
                headline,
                self._chat_id,
                error,
            )
            return (False, "")
        timeout = min(float(expiry_seconds), MAX_APPROVAL_WAIT_SECONDS)
        approved, decided_by = self._broker.wait(approval_id, timeout=timeout)
        # Clearing the keyboard is part of the edit: a late click cannot re-fire.
        self._edit(message_id, _outcome_text(headline, approved=approved, decided_by=decided_by))
        if approved:
            # Keyed on call_id, not "the last prompt": a turn can run tools in
            # parallel, so the receipt has to find its own message.
            self._decided.remember(call_id, message_id=message_id, decided_by=decided_by)
        logger.info(
            "[telegram-gateway] approval headline=%s approved=%s decided_by=%s",
            headline,
            approved,
            decided_by or "(expired)",
        )
        return (approved, decided_by)

    def attach_receipt(self, *, call_id: str, receipt: str) -> None:
        """Replace the approved prompt's outcome with what the call produced."""
        decision = self._decided.take(call_id)
        if decision is None or not receipt.strip():
            return
        self._edit(
            decision.message_id,
            _outcome_text(receipt, approved=True, decided_by=decision.decided_by),
        )

    def _edit(self, message_id: str, text: str) -> None:
        self._client.edit_message_text(
            self._chat_id,
            message_id,
            text,
            reply_markup={"inline_keyboard": []},
        )


def handle_callback_query(
    event: TelegramCallbackQuery,
    *,
    broker: ApprovalBroker,
    client: TelegramBotClient,
    allowed_user_ids: Sequence[str],
) -> bool:
    """Resolve Approve/Deny callback clicks.

    Write-tool approvals always require an explicit allowlist — open chat trust
    must not extend to approving side-effecting tools.
    """
    data = event.data
    if data.startswith(f"{APPROVE_ACTION_ID}:"):
        approval_id = data[len(APPROVE_ACTION_ID) + 1 :]
        approved = True
    elif data.startswith(f"{DENY_ACTION_ID}:"):
        approval_id = data[len(DENY_ACTION_ID) + 1 :]
        approved = False
    else:
        return False

    allowed = set(allowed_user_ids)
    if not allowed or event.user_id not in allowed:
        logger.info(
            "[telegram-gateway] approval click from unauthorized user=%s ignored",
            event.user_id,
        )
        client.answer_callback_query(event.callback_query_id, text="Not authorized")
        return False

    resolved = broker.resolve(
        approval_id,
        approved=approved,
        decided_by=event.user_id,
    )
    client.answer_callback_query(
        event.callback_query_id,
        text="Approved" if approved else "Denied",
    )
    return resolved


_TELEGRAM_CALLBACK_DATA_LIMIT = 64


def _approval_keyboard(approval_id: str) -> dict[str, Any]:
    approve = f"{APPROVE_ACTION_ID}:{approval_id}"
    deny = f"{DENY_ACTION_ID}:{approval_id}"
    # Telegram Bot API rejects callback_data longer than 64 bytes.
    if (
        len(approve.encode("utf-8")) > _TELEGRAM_CALLBACK_DATA_LIMIT
        or len(deny.encode("utf-8")) > _TELEGRAM_CALLBACK_DATA_LIMIT
    ):
        raise ValueError(
            f"Telegram callback_data exceeds {_TELEGRAM_CALLBACK_DATA_LIMIT} bytes "
            f"(approve={len(approve.encode('utf-8'))}, deny={len(deny.encode('utf-8'))})"
        )
    return {
        "inline_keyboard": [
            [
                {"text": "Approve", "callback_data": approve},
                {"text": "Deny", "callback_data": deny},
            ]
        ]
    }


def _outcome_text(label: str, *, approved: bool, decided_by: str) -> str:
    if not decided_by:
        return f"⏱ Approval request for {label} expired — action skipped."
    verb = "approved" if approved else "denied"
    return f"🔒 {label} — {verb} by user {decided_by}."


__all__ = [
    "TelegramApprovalPrompter",
    "handle_callback_query",
]
