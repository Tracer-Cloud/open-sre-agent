"""Top-level composition root for the messaging gateway."""

from __future__ import annotations

from app.entrypoints.gateway_slash import gateway_slash_ports_factory
from gateway.runtime.manager import GatewayManager


def start_gateway(*, wait: bool = True) -> GatewayManager:
    """Start the gateway with headless action-tool adapters wired."""
    return GatewayManager(
        slash_ports_factory=gateway_slash_ports_factory,
    ).start_gateway(wait=wait)


def main() -> None:
    start_gateway()


__all__ = ["gateway_slash_ports_factory", "main", "start_gateway"]
