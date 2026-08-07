"""Typed models for Buzz message delivery."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class BuzzDeliveryTarget:
    """Resolved Buzz delivery destination — a relay + channel UUID.

    ``private_key`` and ``auth_tag`` are deliberately excluded from repr so
    failed assertions, tracebacks, or debug logs do not leak the agent's
    Nostr identity.
    """

    relay_url: str = "http://localhost:3000"
    private_key: str = ""
    auth_tag: str = ""
    buzz_path: str = "buzz"
    channel: str = ""

    def __repr__(self) -> str:
        return (
            "BuzzDeliveryTarget("
            f"relay_url={self.relay_url!r}, "
            f"channel={self.channel!r}, "
            f"buzz_path={self.buzz_path!r}, "
            "private_key=<redacted>, auth_tag=<redacted>)"
        )
