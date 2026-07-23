"""Process composition root for the messaging gateway.

``surfaces`` and ``gateway`` are peer packages: gateway must not import
surfaces. This module owns the glue — headless slash ports from the
interactive shell wired into :class:`gateway.runtime.manager.GatewayManager`.

Started by the daemon as ``python -m surfaces.cli.gateway_entry`` (also
``opensre gateway start`` / ``opensre gateway start --foreground``).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from surfaces.interactive_shell.runtime.slash_adapter import (
    SlashPorts,
    headless_slash_ports,
)

if TYPE_CHECKING:
    from gateway.runtime.manager import GatewayManager


def gateway_slash_ports_factory() -> SlashPorts:
    """Build slash runtime ports for non-interactive gateway turns."""
    return headless_slash_ports()


def start_gateway(*, wait: bool = True) -> GatewayManager:
    """Start the gateway with headless slash ports wired for chat turns."""
    from gateway.runtime.manager import GatewayManager

    return GatewayManager(
        slash_ports_factory=gateway_slash_ports_factory,
    ).start_gateway(wait=wait)


def main() -> None:
    start_gateway()


__all__ = ["gateway_slash_ports_factory", "main", "start_gateway"]


if __name__ == "__main__":
    main()
