"""Shared gateway status builder."""

from __future__ import annotations

from gateway.core.process import GATEWAY_LOG_FILE
from infrastructure.terminal.theme import DIM, HIGHLIGHT


def build_gateway_status_lines(
    pid: int | None,
    component_status: dict[str, str],
    *,
    markup: bool = False,
) -> list[str]:
    """Build status text lines for the gateway daemon and its sub-components."""
    if markup:
        from rich.markup import escape

        state = f"[{HIGHLIGHT}]running (pid {pid})[/]" if pid else f"[{DIM}]stopped[/]"
        lines = [
            f"OpenSRE gateway: {state}",
            *(f"  {escape(name)}: {escape(detail)}" for name, detail in component_status.items()),
            f"[{DIM}]logs: {GATEWAY_LOG_FILE}[/]",
        ]
    else:
        state = f"running (pid {pid})" if pid else "stopped"
        lines = [
            f"OpenSRE gateway: {state}",
            *(f"  {name}: {detail}" for name, detail in component_status.items()),
            f"Logs: {GATEWAY_LOG_FILE}",
        ]
    return lines
