"""Slash command: ``/agents`` (registered local AI agent fleet view).

Bare ``/agents`` renders the registered-agents dashboard; subcommands
drill into specific surfaces (currently ``budget``, ``claim``, ``conflicts``,
``release``, with more landing as the monitor-local-agents initiative ships).
"""

from __future__ import annotations

import math
import os
import time
from collections.abc import Callable
from pathlib import Path

from pydantic import ValidationError
from rich.console import Console
from rich.markup import escape

from app.agents.blast_radius import (
    collect_recent_outside_writes,
    collect_recent_write_events,
)
from app.agents.config import (
    agents_config_path,
    load_agents_config,
    set_agent_budget,
)
from app.agents.conflicts import (
    DEFAULT_WINDOW_SECONDS,
    detect_conflicts,
    render_conflicts,
)
from app.agents.coordination import BranchClaims
from app.agents.lifecycle import TerminateResult, terminate
from app.agents.network_egress import collect_recent_egress_events
from app.agents.probe import probe
from app.agents.registry import AgentRecord, AgentRegistry
from app.agents.sudo_invocations import collect_recent_sudo_events
from app.analytics.events import Event
from app.analytics.provider import get_analytics
from app.cli.interactive_shell.agents_view import render_agents_table
from app.cli.interactive_shell.blast_radius_view import render_blast_radius_panel
from app.cli.interactive_shell.command_registry.types import SlashCommand
from app.cli.interactive_shell.rendering import repl_table
from app.cli.interactive_shell.session import ReplSession
from app.cli.interactive_shell.theme import BOLD_BRAND, DIM, ERROR, HIGHLIGHT, WARNING

_AGENTS_FIRST_ARGS: tuple[tuple[str, str], ...] = (
    ("budget", "view or edit per-agent hourly budgets"),
    ("claim", "claim a branch for an agent"),
    ("conflicts", "show file-write conflicts between local AI agents"),
    ("inspect", "show /agents inspect <pid> blast-radius panel for one agent"),
    ("kill", "SIGTERM → SIGKILL a local agent by PID"),
    ("release", "release a branch claim"),
)


def _opensre_agent_id() -> str:
    return f"opensre:{os.getpid()}"


def _display_path(path: Path) -> str:
    """Replace the user's home prefix with ``~`` for cleaner CLI output."""
    try:
        return f"~/{path.relative_to(Path.home())}"
    except ValueError:
        return str(path)


def _print_config_error(console: Console, exc: ValidationError) -> None:
    console.print(f"[{ERROR}]agents.yaml has invalid contents:[/] {escape(str(exc))}")


def _cmd_agents_list(console: Console) -> bool:
    """Render the registered ``AgentRecord`` set as a Rich table.

    Bare ``/agents`` resolves here. The ``$/hr`` cell reads
    ``hourly_budget_usd`` from ``agents.yaml``; the remaining metric
    cells (``cpu%``, ``tokens/min``, ``status``, ``uptime``) still
    render as placeholders until the per-PID sampler and token-meter
    consumer from #1490 land.
    """
    registry = AgentRegistry()
    table = render_agents_table(registry.list())
    console.print(table)
    return True


def _cmd_agents_conflicts(console: Console) -> bool:
    # Write-event collection lives in :mod:`app.agents.blast_radius`. The
    # first call per registered agent lazy-starts a watchdog observer on
    # that agent's project root; subsequent calls reuse it. The conflict
    # detector's ``window_seconds`` doubles as the wall-clock pre-filter
    # cutoff so anchor-based windowing does not surface stale conflicts
    # when no new writes arrive (see ``conflicts.detect_conflicts``).
    registry = AgentRegistry()
    now = time.time()
    events = collect_recent_write_events(registry.list(), since=now - DEFAULT_WINDOW_SECONDS)
    conflicts = detect_conflicts(
        events,
        window_seconds=DEFAULT_WINDOW_SECONDS,
        opensre_agent_id=_opensre_agent_id(),
    )
    console.print(render_conflicts(conflicts))
    return True


def _cmd_agents_claim(session: ReplSession, console: Console, args: list[str]) -> bool:
    """Handle /agents claim <branch> <agent-name>."""
    if len(args) < 2:
        console.print(f"[{ERROR}]Usage:[/] /agents claim <branch> <agent-name>")
        session.mark_latest(ok=False, kind="slash")
        return False

    branch = args[0].strip()
    agent_name = args[1].strip()

    # Look up the PID from the registry for the given agent name
    registry = AgentRegistry()
    pid = None
    for record in registry.list():
        if record.name == agent_name:
            pid = record.pid
            break

    if pid is None:
        console.print(
            f"[{ERROR}]Agent '{escape(agent_name)}' not found in registry. "
            "Use /agents to see registered agents."
        )
        session.mark_latest(ok=False, kind="slash")
        return False

    claims = BranchClaims()
    claim = claims.claim(branch, agent_name, pid)

    if claim is None:
        existing = claims.get(branch)
        assert existing is not None  # claim() only returns None when branch is held
        console.print(
            f"[{ERROR}]Cannot claim:[/] {escape(branch)} is already held by "
            f"{escape(existing.agent_name)} (pid {existing.pid}). "
            "Use /agents release first."
        )
        session.mark_latest(ok=False, kind="slash")
        return False

    console.print(
        f"[{HIGHLIGHT}]Branch {escape(branch)} now held by {escape(agent_name)} (pid {pid}).[/]"
    )
    return True


def _cmd_agents_release(session: ReplSession, console: Console, args: list[str]) -> bool:
    """Handle /agents release <branch>."""
    if len(args) < 1:
        console.print(f"[{ERROR}]Usage:[/] /agents release <branch>")
        session.mark_latest(ok=False, kind="slash")
        return False

    branch = args[0].strip()
    claims = BranchClaims()

    existing = claims.get(branch)
    if existing is None:
        console.print(f"[{ERROR}]{escape(branch)} is not currently held by any agent.")
        session.mark_latest(ok=False, kind="slash")
        return False

    # release() cannot return None here because we confirmed existing is not None above
    removed = claims.release(branch)
    assert removed is not None
    console.print(
        f"[{HIGHLIGHT}]Released {escape(branch)} (was held by {escape(removed.agent_name)}).[/]"
    )
    return True


def _cmd_agents_budget(session: ReplSession, console: Console, args: list[str]) -> bool:
    """View or edit per-agent budgets stored in ``~/.config/opensre/agents.yaml``.

    No args -> render the current budgets as a table. Two args
    (``<agent> <usd>``) -> set ``hourly_budget_usd`` for that agent and
    persist. Anything else -> usage hint.
    """
    if not args:
        try:
            config = load_agents_config()
        except ValidationError as exc:
            _print_config_error(console, exc)
            session.mark_latest(ok=False, kind="slash")
            return True
        if not config.agents:
            console.print(
                f"[{DIM}]no per-agent budgets configured.[/]  "
                "use [bold]/agents budget <agent> <usd>[/bold] to set one."
            )
            return True
        table = repl_table(title="agent budgets", title_style=BOLD_BRAND)
        table.add_column("agent", style="bold")
        table.add_column("hourly $", justify="right")
        table.add_column("progress min", justify="right")
        table.add_column("error %", justify="right")
        for name in sorted(config.agents):
            budget = config.agents[name]
            table.add_row(
                escape(name),
                f"${budget.hourly_budget_usd:.2f}" if budget.hourly_budget_usd is not None else "-",
                str(budget.progress_minutes) if budget.progress_minutes is not None else "-",
                f"{budget.error_rate_pct:.1f}" if budget.error_rate_pct is not None else "-",
            )
        console.print(table)
        return True

    if len(args) != 2:
        console.print(f"[{ERROR}]usage:[/] /agents budget [<agent> <usd>]")
        session.mark_latest(ok=False, kind="slash")
        return True

    name = args[0].strip()
    raw_usd = args[1]
    try:
        usd = float(raw_usd)
    except ValueError:
        console.print(f"[{ERROR}]invalid budget:[/] {escape(raw_usd)} is not a number")
        session.mark_latest(ok=False, kind="slash")
        return True
    # ``nan`` and ``inf`` slip past ``usd <= 0`` because both
    # ``float("nan") <= 0`` and ``float("inf") <= 0`` are ``False``.
    # Without this guard a stored ``nan`` would corrupt agents.yaml
    # (next load fails Pydantic's ``gt=0`` since ``nan > 0`` is
    # ``False``) and ``inf`` would render as ``$inf`` in the dashboard.
    if not math.isfinite(usd) or usd <= 0:
        console.print(f"[{ERROR}]invalid budget:[/] must be a positive finite number")
        session.mark_latest(ok=False, kind="slash")
        return True

    try:
        set_agent_budget(name, usd)
    except ValidationError as exc:
        _print_config_error(console, exc)
        session.mark_latest(ok=False, kind="slash")
        return True

    console.print(
        f"updated [bold]{escape(name)}[/]: ${usd:.2f}/hr -> {_display_path(agents_config_path())}"
    )
    return True


# Type alias for the optional confirmation callback (used for testing).
_ConfirmFn = Callable[[str], str]


def _cmd_agents_kill(
    session: ReplSession,
    console: Console,
    args: list[str],
    *,
    confirm_fn: _ConfirmFn | None = None,
) -> bool:
    """Handle ``/agents kill <pid> [--force]``.

    Sends SIGTERM, waits up to 5 s, then escalates to SIGKILL.
    Asks for confirmation unless ``--force`` is present.
    Emits an ``agent_killed`` analytics event on success.
    """
    force = "--force" in args
    positional = [a for a in args if a != "--force"]

    if not positional:
        console.print(f"[{ERROR}]usage:[/] /agents kill <pid> [--force]")
        session.mark_latest(ok=False, kind="slash")
        return True

    raw_pid = positional[0]
    try:
        pid = int(raw_pid)
    except ValueError:
        console.print(f"[{ERROR}]invalid pid:[/] {escape(raw_pid)} is not an integer")
        session.mark_latest(ok=False, kind="slash")
        return True

    if pid == os.getpid():
        console.print(f"[{ERROR}]refusing to kill the opensre process itself[/]")
        session.mark_latest(ok=False, kind="slash")
        return True

    # Look up agent name from registry for friendlier output.
    registry = AgentRegistry()
    record = registry.get(pid)
    label = f"{record.name} (pid {pid})" if record else f"pid {pid}"

    if not force:
        prompt_text = f"About to SIGTERM {label}. Confirm? [y/N] "
        if confirm_fn is not None:
            answer = confirm_fn(prompt_text)
        else:
            answer = console.input(prompt_text)
        if answer.strip().lower() not in ("y", "yes"):
            console.print(f"[{DIM}]aborted.[/]")
            return True

    try:
        result: TerminateResult = terminate(pid)
    except ProcessLookupError:
        console.print(f"[{ERROR}]no such process:[/] pid {pid}")
        session.mark_latest(ok=False, kind="slash")
        return True
    except PermissionError:
        console.print(f"[{ERROR}]permission denied:[/] cannot signal pid {pid}")
        session.mark_latest(ok=False, kind="slash")
        return True

    if result.exited:
        console.print(
            f"[{HIGHLIGHT}]Sent {result.signal_sent}. "
            f"Process exited after {result.elapsed_seconds:.1f}s.[/]"
        )
    else:
        console.print(
            f"[{WARNING}]Sent {result.signal_sent} but process may still be running "
            f"after {result.elapsed_seconds:.1f}s.[/]"
        )
        session.mark_latest(ok=False, kind="slash")

    # Remove from the agent registry so `/agents` no longer shows the dead PID.
    # Only forget when the process actually exited — otherwise it stays visible
    # for further monitoring or another kill attempt.
    if record is not None and result.exited:
        registry.forget(pid)

    event = Event.AGENT_KILLED if result.exited else Event.AGENT_KILL_FAILED
    get_analytics().capture(
        event,
        {
            "pid": str(pid),
            "agent_name": record.name if record else "unknown",
            "signal": result.signal_sent,
            "exited": result.exited,
            "elapsed_seconds": str(round(result.elapsed_seconds, 2)),
        },
    )
    return True


def _cmd_agents_inspect(session: ReplSession, console: Console, args: list[str]) -> bool:
    """Handle ``/agents inspect <pid>`` — render the three-section Blast radius panel.

    Resolves the agent's project root and start time on first call; the
    three watcher coordinators (file-write outside-only, sudo
    invocations, network egress) lazy-start on first call too. Subsequent
    calls reuse the running watchers and just re-render. The panel
    header carries an explicit caveat that "since started" really means
    "since the watchers started" because the lazy-start pattern can't
    retroactively observe events that happened before the first inspect.
    """
    if not args:
        console.print(f"[{ERROR}]usage:[/] /agents inspect <pid>")
        session.mark_latest(ok=False, kind="slash")
        return True

    raw_pid = args[0]
    try:
        pid = int(raw_pid)
    except ValueError:
        console.print(f"[{ERROR}]invalid pid:[/] {escape(raw_pid)} is not an integer")
        session.mark_latest(ok=False, kind="slash")
        return True

    registry = AgentRegistry()
    record = registry.get(pid)
    if record is None:
        console.print(
            f"[{ERROR}]no registered agent with pid {pid}.[/] "
            "Use [bold]/agents[/bold] to see registered agents."
        )
        session.mark_latest(ok=False, kind="slash")
        return True

    # ``since=0.0`` so we get every event the watchers have collected;
    # the inspect panel is the "what's happened since I started
    # watching" view, not a sliding window. Lazy-start happens inside
    # each coordinator on first call per ``(name, pid)``.
    #
    # Order matters: collectors run FIRST so the BlastRadiusWatcher
    # is in the cache by the time we need its ``project_root`` for the
    # header. Reading from the cache avoids a second
    # ``psutil.Process(pid).cwd()`` call per render.
    write_events = collect_recent_outside_writes([record], since=0.0)
    sudo_events = collect_recent_sudo_events([record], since=0.0)
    egress_events = collect_recent_egress_events([record], since=0.0)

    # Best-effort process metadata. Both fall back to ``None`` if the
    # process is gone or inaccessible — the panel renders with degraded
    # header rather than failing the command.
    snapshot = probe(pid, cpu_interval=0.0)
    started_at = snapshot.started_at if snapshot is not None else None
    project_root = _project_root_for_inspect(record)

    panel = render_blast_radius_panel(
        record=record,
        project_root=project_root,
        started_at=started_at,
        write_events=write_events,
        sudo_events=sudo_events,
        egress_events=egress_events,
    )
    console.print(panel)
    return True


def _project_root_for_inspect(record: AgentRecord) -> str | None:
    """Best-effort project-root resolver for the inspect panel header.

    Thin wrapper around :func:`app.agents.blast_radius.get_project_root`
    that stringifies the result. The blast-radius helper handles the
    cache lookup and the negative-cache short-circuit so we don't
    re-fire ``psutil.Process(pid).cwd()`` against stale registry
    entries on every render.
    """
    # Imported lazily to avoid pulling watchdog into modules that don't
    # actually use the watcher (the conflict detector and inspect panel
    # both need it; modules like /agents budget don't).
    from app.agents.blast_radius import get_project_root

    root = get_project_root(record)
    return str(root) if root is not None else None


def _cmd_agents(session: ReplSession, console: Console, args: list[str]) -> bool:
    if not args:
        return _cmd_agents_list(console)

    sub = args[0].lower().strip()

    if sub == "budget":
        return _cmd_agents_budget(session, console, args[1:])
    if sub == "conflicts":
        return _cmd_agents_conflicts(console)

    if sub == "claim":
        return _cmd_agents_claim(session, console, args[1:])

    if sub == "inspect":
        return _cmd_agents_inspect(session, console, args[1:])

    if sub == "kill":
        return _cmd_agents_kill(session, console, args[1:])

    if sub == "release":
        return _cmd_agents_release(session, console, args[1:])

    console.print(
        f"[{ERROR}]unknown subcommand:[/] {escape(sub)}  "
        "(try [bold]/agents[/bold], [bold]/agents budget[/bold], "
        "[bold]/agents conflicts[/bold], [bold]/agents inspect[/bold], "
        "[bold]/agents kill[/bold], [bold]/agents claim[/bold], "
        "or [bold]/agents release[/bold])"
    )
    session.mark_latest(ok=False, kind="slash")
    return True


COMMANDS: list[SlashCommand] = [
    SlashCommand(
        "/agents",
        "show registered local AI agents "
        "(subcommands: budget, claim, conflicts, inspect, kill, release)",
        _cmd_agents,
        first_arg_completions=_AGENTS_FIRST_ARGS,
    ),
]

__all__ = ["COMMANDS"]
