"""Gateway slash-runtime composition helpers."""

from __future__ import annotations

from surfaces.interactive_shell.runtime.slash_adapter import (
    SlashPorts,
    headless_slash_ports,
)


def gateway_slash_ports_factory() -> SlashPorts:
    """Build slash runtime ports for non-interactive gateway turns."""
    return headless_slash_ports()


__all__ = ["gateway_slash_ports_factory"]
