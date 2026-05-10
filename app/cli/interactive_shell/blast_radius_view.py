"""Rich rendering for the ``/agents inspect <pid>`` blast-radius panel.

Renders the three-section panel called for in issue #1500's acceptance
criterion: writes outside the project root, sudo invocations, and
new-host network connections — all observed since the watchers
started for that agent.

Lives outside ``app/agents/`` for the same reason as
:mod:`app.cli.interactive_shell.agents_view`: the agents package is
for *collectors*, must not depend on Rich, and the slash-command
layer is the one and only consumer of these renderers.
"""

from __future__ import annotations

from collections.abc import Iterable
from datetime import UTC, datetime

from rich.console import Group, RenderableType
from rich.markup import escape
from rich.panel import Panel
from rich.table import Table

from app.agents.blast_radius import BlastRadiusEvent
from app.agents.network_egress import NetworkEgressEvent
from app.agents.registry import AgentRecord
from app.agents.sudo_invocations import SudoInvocationEvent
from app.cli.interactive_shell.rendering import repl_table
from app.cli.interactive_shell.theme import BOLD_BRAND, DIM, HIGHLIGHT, WARNING

# Section caption when a stream has no events. Distinguishes "watcher
# is running but found nothing" from "watcher couldn't start" so the
# user knows the panel is live, just quiet.
_EMPTY_SECTION = "[dim](nothing observed yet — watcher running)[/]"


def _format_timestamp(t: float) -> str:
    return datetime.fromtimestamp(t, tz=UTC).strftime("%H:%M:%S UTC")


def _writes_outside_table(events: Iterable[BlastRadiusEvent]) -> Table | RenderableType:
    """Render the 'writes outside project root' section."""
    materialized = sorted(events, key=lambda e: e.timestamp, reverse=True)
    if not materialized:
        return _EMPTY_SECTION
    table = repl_table(title="writes outside project root", title_style=WARNING)
    table.add_column("path", style="bold", overflow="fold")
    table.add_column("at", style=DIM)
    for evt in materialized:
        table.add_row(escape(evt.path), _format_timestamp(evt.timestamp))
    return table


def _sudo_table(events: Iterable[SudoInvocationEvent]) -> Table | RenderableType:
    """Render the 'sudo invocations' section."""
    materialized = sorted(events, key=lambda e: e.timestamp, reverse=True)
    if not materialized:
        return _EMPTY_SECTION
    table = repl_table(title="sudo invocations", title_style=WARNING)
    table.add_column("command", style="bold", overflow="fold")
    table.add_column("child pid", justify="right")
    table.add_column("at", style=DIM)
    for evt in materialized:
        table.add_row(
            escape(evt.command),
            str(evt.child_pid),
            _format_timestamp(evt.timestamp),
        )
    return table


def _network_table(events: Iterable[NetworkEgressEvent]) -> Table | RenderableType:
    """Render the 'new outbound network connections' section."""
    materialized = sorted(events, key=lambda e: e.timestamp, reverse=True)
    if not materialized:
        return _EMPTY_SECTION
    table = repl_table(title="new-host network connections", title_style=WARNING)
    table.add_column("remote", style="bold", overflow="fold")
    table.add_column("port", justify="right")
    table.add_column("family", style=DIM)
    table.add_column("at", style=DIM)
    for evt in materialized:
        table.add_row(
            escape(evt.remote_host),
            str(evt.remote_port),
            evt.family,
            _format_timestamp(evt.timestamp),
        )
    return table


def render_blast_radius_panel(
    *,
    record: AgentRecord,
    project_root: str | None,
    started_at: datetime | None,
    write_events: Iterable[BlastRadiusEvent],
    sudo_events: Iterable[SudoInvocationEvent],
    egress_events: Iterable[NetworkEgressEvent],
) -> Panel:
    """Return the three-section Blast radius panel for a single agent.

    ``project_root`` may be ``None`` if the agent's project root could
    not be resolved (no ``.git`` ancestor, or PID gone). ``started_at``
    likewise may be ``None`` if the agent process is no longer
    introspectable; the header degrades gracefully.

    The header carries:
      - agent name and PID
      - process start time + uptime (when known)
      - resolved project root (when known)
      - a one-line caveat noting the watchers' lazy-start semantic so
        users know "since started" really means "since first inspect"
    """
    header_lines: list[str] = [
        f"[{HIGHLIGHT}]{escape(record.name)}[/] [dim](pid {record.pid})[/]",
    ]
    if started_at is not None:
        uptime = datetime.now(UTC) - started_at
        # Drop microseconds — the panel's a glance view, not a stopwatch.
        uptime_str = str(uptime).split(".")[0]
        header_lines.append(
            f"[dim]started:[/] {started_at.strftime('%Y-%m-%d %H:%M:%S UTC')}  "
            f"[dim]uptime:[/] {uptime_str}"
        )
    if project_root is not None:
        header_lines.append(f"[dim]project root:[/] {escape(project_root)}")
    header_lines.append(
        "[dim]watchers lazy-start on first inspect; events shown are since "
        "the watcher started, not the agent.[/]"
    )

    body = Group(
        *header_lines,
        "",
        _writes_outside_table(write_events),
        "",
        _sudo_table(sudo_events),
        "",
        _network_table(egress_events),
    )
    return Panel(body, title="Blast radius", title_align="left", border_style=BOLD_BRAND)


__all__ = ["render_blast_radius_panel"]
