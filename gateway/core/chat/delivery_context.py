"""ContextVar for tracking chat delivery target during gateway turns."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar, Token

from gateway.core.chat.delivery_target import ChatDeliveryTarget

# ContextVar to hold the delivery target for the current turn
_delivery_target: ContextVar[ChatDeliveryTarget | None] = ContextVar(
    "chat_delivery_target", default=None
)


def get_current_delivery_target() -> ChatDeliveryTarget | None:
    """Get the delivery target for the current turn, if any."""
    return _delivery_target.get()


@contextmanager
def bound_delivery_target(target: ChatDeliveryTarget) -> Iterator[None]:
    """Context manager to bind a delivery target for the duration of a turn."""
    token: Token[ChatDeliveryTarget | None] = _delivery_target.set(target)
    try:
        yield
    finally:
        _delivery_target.reset(token)
