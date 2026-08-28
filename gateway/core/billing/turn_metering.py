"""Bind one chat turn's credit request and admit it inside the shared runner."""

from __future__ import annotations

from collections.abc import Callable, Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass

from gateway.core.billing.credits_client import CreditsOutcome, consume_credits


@dataclass(frozen=True, slots=True)
class TurnMeteringRequest:
    """Credit request attached to the current transport turn."""

    organization_id: str
    reason: str
    on_denied: Callable[[], None]


_CURRENT_REQUEST: ContextVar[TurnMeteringRequest | None] = ContextVar(
    "gateway_turn_metering_request", default=None
)


@contextmanager
def bound_turn_metering(
    *,
    organization_id: str,
    reason: str,
    on_denied: Callable[[], None],
) -> Iterator[None]:
    """Bind the credit request consumed after shared capacity admission."""
    token = _CURRENT_REQUEST.set(
        TurnMeteringRequest(
            organization_id=organization_id,
            reason=reason,
            on_denied=on_denied,
        )
    )
    try:
        yield
    finally:
        _CURRENT_REQUEST.reset(token)


def admit_metered_turn() -> bool:
    """Consume the bound turn's credit and report whether agent work may run."""
    request = _CURRENT_REQUEST.get()
    if request is None:
        raise RuntimeError("gateway turn has no bound metering request")
    outcome = consume_credits(request.organization_id, reason=request.reason)
    if outcome is not CreditsOutcome.DENIED:
        return True
    request.on_denied()
    return False


__all__ = [
    "TurnMeteringRequest",
    "admit_metered_turn",
    "bound_turn_metering",
]
