"""Rich-table rendering for the ``/agents`` slash-command dashboard.

Columns: ``agent``, ``pid``, ``uptime``, ``cpu%``, ``tokens/min``,
``$/hr``, ``status``. Every metric cell falls back to ``-`` when its
sampler accessor returns ``None``. ``0`` versus ``-`` is meaningful
in ``tokens/min``: ``0`` is observed-but-idle, ``-`` is unobservable
(no meter for this provider, or the JSONL is unreadable, or the
sampler task is not running — e.g. non-interactive
``opensre agents list``).

This module lives outside ``app/agents/`` so collectors don't pull
in Rich.
"""

from __future__ import annotations

from collections.abc import Iterable
from datetime import UTC, datetime, timedelta

from rich.console import JustifyMethod
from rich.markup import escape
from rich.table import Table

from app.agents.registry import AgentRecord
from app.agents.sampler import get_snapshot, get_tokens_per_min, get_usd_per_hour
from app.agents.status import Status, compute_status
from app.cli.interactive_shell.ui.theme import BOLD_BRAND

_UNFILLED = "-"

# Re-using Rich's own ``JustifyMethod`` so column-justify options
# stay in lockstep with the library.
_COLUMNS: tuple[tuple[str, JustifyMethod], ...] = (
    ("agent", "left"),
    ("pid", "right"),
    ("uptime", "right"),
    ("cpu%", "right"),
    ("tokens/min", "right"),
    ("$/hr", "right"),
    ("status", "left"),
)

_STATUS_COLORS: dict[Status, str] = {
    Status.ACTIVE: "green",
    Status.IDLE: "yellow",
    Status.STUCK: "red",
}


def _format_uptime(delta: timedelta) -> str:
    total_seconds = int(delta.total_seconds())
    if total_seconds < 60:
        return f"{total_seconds}s"
    if total_seconds < 3600:
        return f"{total_seconds // 60}m"
    hours = total_seconds // 3600
    if hours < 24:
        minutes = (total_seconds % 3600) // 60
        return f"{hours}h{minutes}m"
    days = hours // 24
    remaining_hours = hours % 24
    return f"{days}d{remaining_hours}h"


def _format_tokens_per_min(value: float | None) -> str:
    if value is None:
        return _UNFILLED
    # Round-then-compare so ``999.6`` doesn't render as 4-digit ``"1000"``
    # next to its 3-digit neighbors.
    rounded = int(round(value))
    if rounded < 1000:
        return f"{rounded}"
    return f"{value / 1000:.1f}k"


def _format_usd_per_hour(value: float | None) -> str:
    if value is None:
        return _UNFILLED
    return f"${value:.2f}"


def _format_status(status: Status, msg: str = "") -> str:
    """Return a Rich-markup-colorized status cell for the /agents table."""
    color = _STATUS_COLORS.get(status, "default")
    label = f"{status.value} ({msg})" if msg else status.value
    return f"[{color}]{label}[/{color}]"


def render_agents_table(records: Iterable[AgentRecord]) -> Table:
    materialized = list(records)
    table = Table(
        title="agents",
        title_style=BOLD_BRAND,
        caption="no agents discovered or registered yet" if not materialized else None,
    )
    for header, justify in _COLUMNS:
        table.add_column(header, justify=justify)
    now = datetime.now(UTC)
    for record in materialized:
        snapshot = get_snapshot(record.pid)
        if snapshot is not None:
            # last_output_at requires a background collector that tracks when each agent last wrote
            # to stdout — not yet implemented. Until that lands, the fallback to snapshot.started_at
            # means the heuristic overestimates silence duration for agents that are actively producing output.
            status = compute_status(
                snapshot,
                now,
                last_output_at=None,
                idle_after_s=120,
                stuck_after_s=480,
            )
            status_msg = ""
            if status is Status.STUCK:
                status_msg = f"{_format_uptime(now - snapshot.started_at)} no progress"

            uptime_cell = _format_uptime(now - snapshot.started_at)
            cpu_cell = f"{snapshot.cpu_percent:.1f}"
            status_cell = _format_status(status, status_msg)
        else:
            uptime_cell = _UNFILLED
            cpu_cell = _UNFILLED
            status_cell = _UNFILLED
        tokens_cell = _format_tokens_per_min(get_tokens_per_min(record.pid))
        hourly_cell = _format_usd_per_hour(get_usd_per_hour(record.pid))
        table.add_row(
            escape(record.name),
            str(record.pid),
            uptime_cell,
            cpu_cell,
            tokens_cell,
            hourly_cell,
            status_cell,
        )
    return table


__all__ = ["render_agents_table"]
