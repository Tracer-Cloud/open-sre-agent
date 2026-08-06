"""Transport-neutral approval gate for write tools in gateway turns.

Tools registered with ``requires_approval=True`` declare a human-in-the-loop
contract that the CLI honours with a ``Proceed? [Y/n]`` prompt. Chat gateways
honour it by posting Approve / Deny buttons and blocking the tool call until an
authorized member clicks or the request expires.

This module holds the parts that do not depend on a chat transport: the broker
that connects a click to the waiting tool call, the button identifiers, and the
harness hooks. Each transport supplies its own :class:`ApprovalPrompter` — Block
Kit in ``gateway.transports.slack.approvals``, message components in
``gateway.transports.discord.approvals``.
"""

from __future__ import annotations

import json
import threading
import uuid
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any, Protocol

from core.execution import BeforeToolCallResult, ToolExecutionHooks, ToolExecutionRequest
from gateway.core.runtime.security_audit import audit_security_action

APPROVE_ACTION_ID = "opensre_approval_approve"
DENY_ACTION_ID = "opensre_approval_deny"

# Never let one approval outlive the turn timeout (240s default): a prompt
# nobody answers should resolve to deny while the turn can still say so.
MAX_APPROVAL_WAIT_SECONDS = 180.0
# Longest argument preview rendered into an approval prompt.
ARGS_PREVIEW_LIMIT = 400


@dataclass
class _PendingApproval:
    event: threading.Event = field(default_factory=threading.Event)
    approved: bool = False
    decided_by: str = ""
    platform: str = ""
    chat_id: str = ""


class ApprovalBroker:
    """Thread-safe registry connecting button clicks to waiting tool calls."""

    def __init__(self) -> None:
        self._pending: dict[str, _PendingApproval] = {}
        self._lock = threading.Lock()

    def create(
        self,
        *,
        platform: str | None = None,
        chat_id: str | None = None,
    ) -> str:
        approval_id = uuid.uuid4().hex
        with self._lock:
            self._pending[approval_id] = _PendingApproval(
                platform=platform or "",
                chat_id=chat_id or "",
            )
        audit_security_action(
            action="approval.create",
            platform=platform,
            chat_id=chat_id,
            resource_type="approval",
            resource_id=approval_id,
            outcome="pending",
        )
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
            platform = pending.platform
            chat_id = pending.chat_id
        audit_security_action(
            action="approval.resolve",
            platform=platform or None,
            chat_id=chat_id or None,
            actor_id=decided_by,
            resource_type="approval",
            resource_id=approval_id,
            outcome="approved" if approved else "denied",
        )
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
            audit_security_action(
                action="approval.expire",
                platform=pending.platform or None,
                chat_id=pending.chat_id or None,
                resource_type="approval",
                resource_id=approval_id,
                outcome="denied",
            )
            return (False, "")
        return (pending.approved, pending.decided_by)


class ApprovalPrompter(Protocol):
    """Asks one conversation for approval and reports the decision."""

    def request(
        self,
        *,
        tool_name: str,
        reason: str,
        arguments: Mapping[str, Any],
        expiry_seconds: float,
    ) -> tuple[bool, str]:
        """Return (approved, id of the member who decided; empty when expired)."""


def approval_tool_hooks(prompter: ApprovalPrompter) -> ToolExecutionHooks:
    """Tool hooks enforcing ``requires_approval`` through chat buttons."""

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


def arguments_preview(arguments: Mapping[str, Any]) -> str:
    """Render tool arguments for a prompt, truncated to a readable length."""
    if not arguments:
        return ""
    try:
        preview = json.dumps(dict(arguments), ensure_ascii=False, default=str)
    except Exception:
        preview = str(dict(arguments))
    if len(preview) > ARGS_PREVIEW_LIMIT:
        preview = preview[: ARGS_PREVIEW_LIMIT - 1] + "…"
    return preview


__all__ = [
    "APPROVE_ACTION_ID",
    "ARGS_PREVIEW_LIMIT",
    "DENY_ACTION_ID",
    "MAX_APPROVAL_WAIT_SECONDS",
    "ApprovalBroker",
    "ApprovalPrompter",
    "approval_tool_hooks",
    "arguments_preview",
]
