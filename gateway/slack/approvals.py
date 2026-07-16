"""Block Kit approval gate for write actions in Slack gateway turns.

Tools registered with ``requires_approval=True`` (message sends, channel
joins, code-fix runs) declare a human-in-the-loop contract that the CLI
honours with a ``Proceed? [Y/n]`` prompt but gateway turns silently ignored.
This module enforces it in Slack: a ``before_tool_call`` hook posts an
Approve / Deny button message in the triggering thread and blocks the tool
call until an authorized member clicks or the request expires (deny).

Pieces:

- :class:`ApprovalBroker` — process-wide registry of pending requests,
  resolved from ``block_actions`` click payloads on the Socket Mode
  connection.
- :class:`ThreadApprovalPrompter` — bound to one turn's channel + thread;
  posts the button message, waits, and rewrites it with the outcome.
- :func:`approval_tool_hooks` — packages the prompter as
  :class:`~core.execution.ToolExecutionHooks` for the agent harness.
"""

from __future__ import annotations

import json
import logging
import threading
import uuid
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

from core.execution import BeforeToolCallResult, ToolExecutionHooks, ToolExecutionRequest
from gateway.slack.client import SlackMessagingClient

logger = logging.getLogger("gateway")

APPROVE_ACTION_ID = "opensre_approval_approve"
DENY_ACTION_ID = "opensre_approval_deny"

# Never let one approval outlive the turn timeout (240s default): a prompt
# nobody answers should resolve to deny while the turn can still say so.
_MAX_APPROVAL_WAIT_SECONDS = 180.0
_ARGS_PREVIEW_LIMIT = 400


@dataclass
class _PendingApproval:
    event: threading.Event = field(default_factory=threading.Event)
    approved: bool = False
    decided_by: str = ""


class ApprovalBroker:
    """Thread-safe registry connecting button clicks to waiting tool calls."""

    def __init__(self) -> None:
        self._pending: dict[str, _PendingApproval] = {}
        self._lock = threading.Lock()

    def create(self) -> str:
        approval_id = uuid.uuid4().hex
        with self._lock:
            self._pending[approval_id] = _PendingApproval()
        return approval_id

    def resolve(self, approval_id: str, *, approved: bool, decided_by: str) -> bool:
        """Deliver a click decision; False when unknown/expired/already decided."""
        with self._lock:
            pending = self._pending.get(approval_id)
            if pending is None or pending.event.is_set():
                return False
            pending.approved = approved
            pending.decided_by = decided_by
            pending.event.set()
            return True

    def wait(self, approval_id: str, *, timeout: float) -> tuple[bool, str]:
        """Block for a decision; expiry counts as deny. Returns (approved, decided_by)."""
        with self._lock:
            pending = self._pending.get(approval_id)
        if pending is None:
            return (False, "")
        decided = pending.event.wait(timeout)
        with self._lock:
            self._pending.pop(approval_id, None)
        if not decided:
            return (False, "")
        return (pending.approved, pending.decided_by)


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

    def request(
        self,
        *,
        tool_name: str,
        reason: str,
        arguments: Mapping[str, Any],
        expiry_seconds: float,
    ) -> tuple[bool, str]:
        """Ask the thread for approval; returns (approved, decided_by user id)."""
        approval_id = self._broker.create()
        prompt_text = _prompt_text(tool_name, reason)
        message_ts = self._client.post_message(
            channel=self._channel_id,
            text=f"Approval needed: {tool_name} — approve or deny in Slack.",
            thread_ts=self._thread_ts,
            blocks=_prompt_blocks(approval_id, prompt_text, arguments),
        )
        if message_ts is None:
            # No buttons on screen means nobody can approve: fail closed.
            logger.warning(
                "[slack-gateway] approval prompt post failed tool=%s channel=%s",
                tool_name,
                self._channel_id,
            )
            return (False, "")
        timeout = min(float(expiry_seconds), _MAX_APPROVAL_WAIT_SECONDS)
        approved, decided_by = self._broker.wait(approval_id, timeout=timeout)
        self._client.update_message(
            channel=self._channel_id,
            ts=message_ts,
            text=_outcome_text(tool_name, approved=approved, decided_by=decided_by),
        )
        logger.info(
            "[slack-gateway] approval tool=%s approved=%s decided_by=%s",
            tool_name,
            approved,
            decided_by or "(expired)",
        )
        return (approved, decided_by)


def approval_tool_hooks(prompter: ThreadApprovalPrompter) -> ToolExecutionHooks:
    """Tool hooks enforcing ``requires_approval`` through Slack buttons."""

    def before_tool_call(request: ToolExecutionRequest) -> BeforeToolCallResult | None:
        tool = request.tool
        if not bool(getattr(tool, "requires_approval", False)):
            return None
        approved, decided_by = prompter.request(
            tool_name=request.tool_call.name,
            reason=str(getattr(tool, "approval_reason", "") or ""),
            arguments=request.arguments,
            expiry_seconds=float(getattr(tool, "approval_expiry_seconds", 300)),
        )
        if approved:
            return BeforeToolCallResult(approved=True)
        who = f"<@{decided_by}>" if decided_by else "nobody (request expired)"
        return BeforeToolCallResult(
            blocked=True,
            reason=(
                f"The user denied approval for {request.tool_call.name} "
                f"(decision by {who}). Do not retry; tell the user what you "
                "wanted to do and why."
            ),
        )

    return ToolExecutionHooks(before_tool_call=before_tool_call)


def handle_block_actions_payload(
    payload: Mapping[str, Any],
    *,
    broker: ApprovalBroker,
    allowed_user_ids: list[str],
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
    if allowed_user_ids and user_id not in allowed_user_ids:
        logger.info("[slack-gateway] approval click from unauthorized user=%s ignored", user_id)
        return False
    if not allowed_user_ids and not allow_open_workspace:
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


def _prompt_text(tool_name: str, reason: str) -> str:
    line = f":lock: *Approval needed — `{tool_name}`*"
    if reason.strip():
        line += f"\n{reason.strip()}"
    return line


def _prompt_blocks(
    approval_id: str,
    prompt_text: str,
    arguments: Mapping[str, Any],
) -> list[dict[str, Any]]:
    preview = _arguments_preview(arguments)
    section_text = prompt_text if not preview else f"{prompt_text}\n```{preview}```"
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


def _arguments_preview(arguments: Mapping[str, Any]) -> str:
    if not arguments:
        return ""
    try:
        preview = json.dumps(dict(arguments), ensure_ascii=False, default=str)
    except Exception:
        preview = str(dict(arguments))
    if len(preview) > _ARGS_PREVIEW_LIMIT:
        preview = preview[: _ARGS_PREVIEW_LIMIT - 1] + "…"
    return preview


def _outcome_text(tool_name: str, *, approved: bool, decided_by: str) -> str:
    if approved:
        return f":white_check_mark: `{tool_name}` approved by <@{decided_by}>"
    if decided_by:
        return f":no_entry: `{tool_name}` denied by <@{decided_by}>"
    return f":hourglass: Approval request for `{tool_name}` expired — action skipped."


__all__ = [
    "APPROVE_ACTION_ID",
    "DENY_ACTION_ID",
    "ApprovalBroker",
    "ThreadApprovalPrompter",
    "approval_tool_hooks",
    "handle_block_actions_payload",
]
