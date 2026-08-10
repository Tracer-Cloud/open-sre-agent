"""Chat integration contracts for detached investigation delivery."""

from __future__ import annotations

from gateway.core.chat.delivery_context import bound_delivery_target, get_current_delivery_target
from gateway.core.chat.delivery_target import ChatDeliveryTarget
from gateway.core.chat.notifier import (
    ChatNotifier,
    DetachedInvestigationAck,
    DetachedInvestigationRequest,
)
from gateway.core.chat.registry import (
    ChatNotifierRegistry,
    get_chat_notifier,
    get_chat_notifier_registry,
    reset_chat_notifier_registry_for_tests,
)

__all__ = [
    "ChatDeliveryTarget",
    "ChatNotifier",
    "ChatNotifierRegistry",
    "DetachedInvestigationAck",
    "DetachedInvestigationRequest",
    "bound_delivery_target",
    "get_chat_notifier",
    "get_chat_notifier_registry",
    "get_current_delivery_target",
    "reset_chat_notifier_registry_for_tests",
]
