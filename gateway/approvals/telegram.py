"""Telegram inline-keyboard approval flow for gateway tool execution."""

from __future__ import annotations

import hashlib
import json
import logging
import threading
import time
from dataclasses import dataclass, field
from typing import Any

from core.execution import BeforeToolCallResult, ToolExecutionHooks, ToolExecutionRequest
from gateway.approvals.store import ApprovalStore
from gateway.config import GatewaySettings
from gateway.platforms.telegram.client import TelegramBotClient

logger = logging.getLogger(__name__)


@dataclass
class _Waiter:
    event: threading.Event = field(default_factory=threading.Event)
    approved: bool = False


class TelegramApprovalService:
    """Manage pending approvals and callback resolution."""

    def __init__(
        self,
        *,
        client: TelegramBotClient,
        store: ApprovalStore,
        settings: GatewaySettings,
    ) -> None:
        self._client = client
        self._store = store
        self._settings = settings
        self._waiters: dict[str, _Waiter] = {}
        self._lock = threading.Lock()

    def handle_callback(self, *, user_id: str, callback_data: str, callback_query_id: str) -> None:
        if not callback_data.startswith(("approve:", "deny:")):
            return
        action, _, approval_id = callback_data.partition(":")
        approved = action == "approve"
        row = self._store.resolve(approval_id, status="approved" if approved else "denied")
        if row is None:
            self._client.answer_callback_query(
                callback_query_id, text="Approval expired or unknown."
            )
            return
        if str(row["chat_id"]) != user_id:
            self._client.answer_callback_query(
                callback_query_id, text="Not authorized for this approval."
            )
            return
        with self._lock:
            waiter = self._waiters.get(approval_id)
            if waiter is not None:
                waiter.approved = approved
                waiter.event.set()
        self._client.answer_callback_query(
            callback_query_id,
            text="Approved." if approved else "Denied.",
        )

    def wait_for_confirmation(self, *, chat_id: str, prompt: str) -> str:
        approved = self._prompt_and_wait(
            chat_id=chat_id,
            tool_name="action_confirm",
            payload={"prompt": prompt},
        )
        return "yes" if approved else "no"

    def _prompt_and_wait(self, *, chat_id: str, tool_name: str, payload: dict[str, Any]) -> bool:
        payload_hash = hashlib.sha256(
            json.dumps(payload, sort_keys=True, default=str).encode()
        ).hexdigest()[:16]
        expires_at = time.time() + self._settings.approval_timeout_seconds
        approval_id = self._store.create(
            chat_id=chat_id,
            message_id="pending",
            tool_name=tool_name,
            payload_hash=payload_hash,
            expires_at=expires_at,
        )
        waiter = _Waiter()
        with self._lock:
            self._waiters[approval_id] = waiter
        try:
            reason = payload.get("prompt") or payload.get("reason") or tool_name
            text = f"Approval required for {tool_name}:\n{reason}"
            self._client.send_message(
                chat_id,
                text,
                reply_markup=TelegramBotClient.approval_keyboard(approval_id),
            )
            if not waiter.event.wait(timeout=self._settings.approval_timeout_seconds):
                return False
            return waiter.approved
        finally:
            with self._lock:
                self._waiters.pop(approval_id, None)

    def before_tool_call(self, request: ToolExecutionRequest) -> BeforeToolCallResult | None:
        tool = request.tool
        requires = bool(getattr(tool, "requires_approval", False))
        side_effect = str(getattr(tool, "side_effect_level", "read_only"))
        if not requires and not (
            self._settings.gate_side_effects and side_effect in {"mutating", "external"}
        ):
            return BeforeToolCallResult(approved=True)

        chat_id = str(request.resolved_integrations.get("_gateway_chat_id") or "")
        if not chat_id:
            return BeforeToolCallResult(
                blocked=True, reason="Missing gateway chat context for approval."
            )

        approved = self._prompt_and_wait(
            chat_id=chat_id,
            tool_name=request.tool_call.name,
            payload={
                "arguments": request.arguments,
                "reason": getattr(tool, "approval_reason", ""),
            },
        )
        if not approved:
            return BeforeToolCallResult(blocked=True, reason="User denied or timed out approval.")
        return BeforeToolCallResult(approved=True)

    def hooks(self) -> ToolExecutionHooks:
        return ToolExecutionHooks(before_tool_call=self.before_tool_call)


def inject_gateway_chat_context(resolved: dict[str, Any], chat_id: str) -> dict[str, Any]:
    merged = dict(resolved)
    merged["_gateway_chat_id"] = chat_id
    return merged
