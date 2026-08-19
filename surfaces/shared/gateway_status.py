"""What `gateway status` says, without deciding how it looks.

``opensre gateway status`` and the REPL's ``/gateway status`` show the same
two things -- the daemon's PID and each component's line from
``read_component_status()`` -- and used to build that list twice. The copies
drifted.

This module owns the content and its order. It deliberately owns nothing about
rendering: the CLI writes plain text through ``click.echo`` and the shell
writes rich markup through ``console.print`` and must escape values first.
A shared printer would have to strip the shell's theme or push markup into
piped CLI output, so the surfaces keep their own.
"""

from __future__ import annotations

from dataclasses import dataclass

from gateway.core.process import (
    GATEWAY_LOG_FILE,
    gateway_daemon_pid,
    read_component_status,
)

#: Label for the trailing log-file line, shared so the two surfaces cannot
#: disagree on it again.
LOGS_LABEL = "Logs"

#: Label for the leading daemon line.
DAEMON_LABEL = "OpenSRE gateway"


@dataclass(frozen=True)
class GatewayStatus:
    """A snapshot of what the two status surfaces render."""

    pid: int | None
    components: tuple[tuple[str, str], ...]
    log_file: str

    @property
    def running(self) -> bool:
        return self.pid is not None

    @property
    def daemon_state(self) -> str:
        """The daemon line's value, without any styling.

        The shell colours running and stopped differently, so it asks
        ``running`` rather than matching on this string.
        """
        return f"running (pid {self.pid})" if self.pid else "stopped"

    def rows(self) -> tuple[tuple[str, str], ...]:
        """Every line as an ordered ``(label, value)`` pair.

        Daemon first, then one row per component in the order
        ``read_component_status()`` returns them, then the log file.
        """
        return (
            (DAEMON_LABEL, self.daemon_state),
            *self.components,
            (LOGS_LABEL, self.log_file),
        )


def read_gateway_status() -> GatewayStatus:
    """Collect the current gateway status."""
    return GatewayStatus(
        pid=gateway_daemon_pid(),
        components=tuple(read_component_status().items()),
        log_file=str(GATEWAY_LOG_FILE),
    )
