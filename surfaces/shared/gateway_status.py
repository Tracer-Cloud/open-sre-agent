"""Status rows shared by ``opensre gateway status`` and the shell's ``/gateway status``.

Turns the two already-fetched reads (``gateway_daemon_pid()``,
``read_component_status()``) into ordered (label, value) rows so the content
and ordering cannot drift between the two surfaces' hand-written copies again.
Each surface still fetches its own data and renders its own way — the shell
still must escape component text before printing it as Rich markup.
"""

from __future__ import annotations

from dataclasses import dataclass

from gateway.core.process import GATEWAY_LOG_FILE


@dataclass(frozen=True)
class GatewayStatusLines:
    """Ordered (label, value) rows for a gateway status display."""

    daemon: tuple[str, str]
    components: list[tuple[str, str]]
    logs: tuple[str, str]


def gateway_status_lines(*, pid: int | None, components: dict[str, str]) -> GatewayStatusLines:
    """Build the ordered status rows from an already-fetched pid and component map."""
    state = f"running (pid {pid})" if pid else "stopped"
    return GatewayStatusLines(
        daemon=("OpenSRE gateway", state),
        components=list(components.items()),
        logs=("Logs", str(GATEWAY_LOG_FILE)),
    )


__all__ = ["GatewayStatusLines", "gateway_status_lines"]
