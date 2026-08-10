"""Block Kit approval prompt for write actions in Slack gateway turns.

The transport-neutral half — broker, button identifiers, harness hooks — lives
in :mod:`gateway.core.runtime.approvals`. This module renders the Slack side: a
:class:`ThreadApprovalPrompter` posts an Approve / Deny button message in the
triggering thread, and :func:`handle_block_actions_payload` routes the
resulting ``block_actions`` click back to the broker.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping, Sequence
from typing import Any

from gateway.core.runtime.approvals import (
    APPROVE_ACTION_ID,
    DENY_ACTION_ID,
    MAX_APPROVAL_WAIT_SECONDS,
    ApprovalBroker,
    DecidedPrompts,
)
from gateway.transports.slack.client import SlackMessagingClient

logger = logging.getLogger("gateway")


class ThreadApprovalPrompter:
    """Posts approval buttons in one turn's thread and waits for the click."""

    def __init__(
        self,
        *,
        client: SlackMessagingClient,
        broker: ApprovalBroker,
        channel_id: str,
        thread_ts: str,
    ) -> None:
        self._client = client
        self._broker = broker
        self._channel_id = channel_id
        self._thread_ts = thread_ts
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
        """Ask the thread for approval; returns (approved, decided_by user id)."""
        approval_id = self._broker.create(
            platform="slack",
            chat_id=self._channel_id,
        )
        message_ts = self._client.post_message(
            channel=self._channel_id,
            text=f"Approval needed: {headline} — approve or deny in Slack.",
            thread_ts=self._thread_ts,
            blocks=_prompt_blocks(approval_id, _prompt_text(headline, reason), details),
        )
        if message_ts is None:
            # No buttons on screen means nobody can approve: fail closed.
            logger.warning(
                "[slack-gateway] approval prompt post failed call=%s channel=%s",
                call_id,
                self._channel_id,
            )
            return (False, "")
        timeout = min(float(expiry_seconds), MAX_APPROVAL_WAIT_SECONDS)
        approved, decided_by = self._broker.wait(approval_id, timeout=timeout)
        self._client.update_message(
            channel=self._channel_id,
            ts=message_ts,
            text=_outcome_text(headline, approved=approved, decided_by=decided_by),
        )
        if approved:
            self._decided.remember(call_id, message_id=message_ts, decided_by=decided_by)
        logger.info(
            "[slack-gateway] approval call=%s approved=%s decided_by=%s",
            call_id,
            approved,
            decided_by or "(expired)",
        )
        return (approved, decided_by)

    def attach_receipt(self, *, call_id: str, receipt: str) -> None:
        """Replace the approved prompt's outcome with what the call produced."""
        decision = self._decided.take(call_id)
        if decision is None or not receipt.strip():
            return
        self._client.update_message(
            channel=self._channel_id,
            ts=decision.message_id,
            text=_outcome_text(receipt, approved=True, decided_by=decision.decided_by),
        )


def handle_block_actions_payload(
    payload: Mapping[str, Any],
    *,
    broker: ApprovalBroker,
    allowed_user_ids: Sequence[str],
    allow_open_workspace: bool,
) -> bool:
    """Route one interactive ``block_actions`` payload to the broker.

    Returns whether a pending approval was resolved. Clicks from users
    outside the allowlist are ignored — buttons are visible to the whole
    channel but only authorized members may decide.
    """
    if str(payload.get("type") or "") != "block_actions":
        return False
    user_id = str((payload.get("user") or {}).get("id") or "")
    actions = payload.get("actions")
    if not user_id or not isinstance(actions, list):
        return False
    allowed_set = set(allowed_user_ids)
    if allowed_set and user_id not in allowed_set:
        logger.info("[slack-gateway] approval click from unauthorized user=%s ignored", user_id)
        return False
    if not allowed_set and not allow_open_workspace:
        return False
    resolved = False
    for action in actions:
        if not isinstance(action, Mapping):
            continue
        action_id = str(action.get("action_id") or "")
        approval_id = str(action.get("value") or "")
        if action_id not in (APPROVE_ACTION_ID, DENY_ACTION_ID) or not approval_id:
            continue
        if broker.resolve(
            approval_id,
            approved=action_id == APPROVE_ACTION_ID,
            decided_by=user_id,
        ):
            resolved = True
    return resolved


def _prompt_text(headline: str, reason: str) -> str:
    line = f":lock: *Approval needed — {headline}*"
    if reason.strip():
        line += f"\n{reason.strip()}"
    return line


def _prompt_blocks(
    approval_id: str,
    prompt_text: str,
    details: str,
) -> list[dict[str, Any]]:
    section_text = prompt_text if not details else f"{prompt_text}\n```{details}```"
    return [
        {"type": "section", "text": {"type": "mrkdwn", "text": section_text}},
        {
            "type": "actions",
            "block_id": f"opensre_approval:{approval_id}",
            "elements": [
                {
                    "type": "button",
                    "text": {"type": "plain_text", "text": "Approve"},
                    "style": "primary",
                    "action_id": APPROVE_ACTION_ID,
                    "value": approval_id,
                },
                {
                    "type": "button",
                    "text": {"type": "plain_text", "text": "Deny"},
                    "style": "danger",
                    "action_id": DENY_ACTION_ID,
                    "value": approval_id,
                },
            ],
        },
    ]


def _outcome_text(label: str, *, approved: bool, decided_by: str) -> str:
    if approved:
        return f":white_check_mark: {label} — approved by <@{decided_by}>"
    if decided_by:
        return f":no_entry: {label} — denied by <@{decided_by}>"
    return f":hourglass: {label} — approval request expired, action skipped."


__all__ = [
    "ThreadApprovalPrompter",
    "handle_block_actions_payload",
]
