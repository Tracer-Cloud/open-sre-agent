"""Slash command: ``/agents`` (registered local AI agent fleet view).

Bare ``/agents`` renders the registered-agents dashboard; subcommands
cover ``budget``, ``bus``, ``claim``, ``conflicts``, ``kill``, ``release``,
and ``trace`` (with more surfaces planned for monitor-local-agents).
"""

from __future__ import annotations

import math
import os
import time
from collections.abc import Callable
from pathlib import Path

from prompt_toolkit.patch_stdout import patch_stdout
from pydantic import ValidationError
from rich.console import Console
from rich.live import Live
from rich.markup import escape
from rich.text import Text

from app.agents.bus import BusMessage, subscribe
from app.agents.config import (
    agents_config_path,
    load_agents_config,
    set_agent_budget,
)
from app.agents.conflicts import (
    DEFAULT_WINDOW_SECONDS,
    WriteEvent,
    detect_conflicts,
    render_conflicts,
)
from app.agents.coordination import BranchClaims
from app.agents.discovery import registered_and_discovered_agents
from app.agents.lifecycle import TerminateResult, terminate
from app.agents.registry import AgentRegistry
from app.agents.tail import AttachSession, AttachUnsupported, attach
from app.analytics.events import Event
from app.analytics.provider import get_analytics
from app.cli.interactive_shell.command_registry.types import SlashCommand
from app.cli.interactive_shell.runtime import ReplSession
from app.cli.interactive_shell.ui import (
    BOLD_BRAND,
    DIM,
    ERROR,
    HIGHLIGHT,
    WARNING,
    render_agents_table,
    repl_table,
)

_AGENTS_FIRST_ARGS: tuple[tuple[str, str], ...] = (
    ("budget", "view or edit per-agent hourly budgets"),
    ("bus", "live-tail the cross-agent context bus"),
    ("claim", "claim a branch for an agent"),
    ("conflicts", "show file-write conflicts between local AI agents"),
    ("kill", "SIGTERM → SIGKILL a local agent by PID"),
    ("release", "release a branch claim"),
    ("trace", "live tail of an agent's stdout by pid"),
)

_TRACE_REFRESH_PER_SECOND = 10
# Match the throttle period to ``Live``'s refresh rate: under a 1k-line/sec
# agent the reader thread can publish chunks faster than Rich actually
# paints, and each ``live.update(Text.from_ansi(...))`` we make in
# excess just creates a Renderable Rich will discard at the next paint.
# Throttling the *call* to ``Live`` to one period bounds CPU under burst
# writers without affecting how fast the screen updates.
_TRACE_RENDER_PERIOD_S = 1.0 / _TRACE_REFRESH_PER_SECOND
# Cap the on-screen render to the most recent slice of the 4 MiB buffer
# so we don't reparse a 4 MiB string through Rich at 10 fps under burst
# writers. A few screens of context is plenty for "what is the agent
# doing right now"; the full tail is still in ``sess.buffer`` for any
# future drill-down view.
_TRACE_RENDER_TAIL_BYTES = 64 * 1024


def _render_trace_snapshot(live: Live, sess: AttachSession) -> None:
    """Decode the bounded snapshot with UTF-8 boundary safety for ``Live``.

    ANSI sequences are interpreted (Rich); treat traced output like unfiltered ``kubectl logs``.
    """
    snapshot = _slice_to_utf8_boundary(sess.buffer.snapshot(), _TRACE_RENDER_TAIL_BYTES)
    live.update(Text.from_ansi(snapshot.decode("utf-8", errors="replace")))


def _slice_to_utf8_boundary(data: bytes, max_bytes: int) -> bytes:
    """Return the suffix of ``data`` that fits in ``max_bytes`` and starts
    on a UTF-8 codepoint boundary.

    A plain ``data[-max_bytes:]`` can land mid-codepoint, which decodes to
    a leading U+FFFD under ``errors="replace"`` — visible as a stray
    replacement character at the top of the live view. ``TailBuffer``
    preserves boundaries by dropping whole chunks; we have to do the same
    once we re-flatten and slice on the render side. UTF-8 continuation
    bytes match ``10xxxxxx`` (``b & 0xC0 == 0x80``); a codepoint is at
    most 4 bytes, so we walk forward at most 3 continuation bytes to
    reach the next start byte.
    """
    if len(data) <= max_bytes:
        return data
    sliced = data[-max_bytes:]
    start = 0
    while start < 4 and start < len(sliced) and (sliced[start] & 0xC0) == 0x80:
        start += 1
    return sliced[start:]


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
    """Render registered plus read-only discovered agents as a Rich table.

    Bare ``/agents`` resolves here. Explicit registry rows keep winning
    on PID collisions; process discovery fills in Cursor, Claude Code,
    Codex, Aider, and Gemini CLI sessions that the user never registered.
    """
    registry = AgentRegistry()
    table = render_agents_table(registered_and_discovered_agents(registry))
    console.print(table)
    return True


def _format_bus_message(msg: BusMessage) -> str:
    """Render one ``BusMessage`` as ``[agent] path — summary`` (path optional)."""
    parts = [f"[{HIGHLIGHT}]\\[{escape(msg.agent)}][/]"]
    if msg.path:
        parts.append(escape(msg.path))
        parts.append("—")
    parts.append(escape(msg.summary))
    return " ".join(parts)


def _cmd_agents_bus(console: Console) -> bool:
    """Live-tail the cross-agent context bus until ``Ctrl-C`` or broker exit.

    Self-elects a broker if none is running, then streams each ``BusMessage``
    as it arrives. The loop ends in three ways, each with explicit feedback:
    ``KeyboardInterrupt`` (user detached), broker disconnect (e.g. the
    publishing process exited), or socket error.
    """
    console.print(
        f"[{DIM}]tailing /agents bus — Ctrl-C to exit[/]",
    )
    try:
        for msg in subscribe():
            console.print(_format_bus_message(msg))
    except KeyboardInterrupt:
        console.print(f"[{DIM}](detached)[/]")
        return True
    except OSError as exc:
        console.print(f"[{ERROR}]bus error:[/] {escape(str(exc))}")
        return False
    # ``subscribe()`` returned cleanly — the broker closed our connection
    # (e.g. it stopped, or its host process exited). Surface that explicitly
    # so the user isn't left wondering why the prompt came back.
    console.print(f"[{DIM}]bus broker disconnected[/]")
    return True


def _cmd_agents_conflicts(console: Console) -> bool:
    # Real write-event collection comes from #1500 (filesystem blast-radius
    # watcher), out of scope for this PR. Until that lands, the event source
    # is empty and `/agents conflicts` reports "no conflicts detected".
    events: list[WriteEvent] = []
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


def _render_live_tail(console: Console, label: str, sess: AttachSession) -> None:
    """kubectl-logs-style render: a single Ctrl+C returns to the prompt.

    Catches :class:`KeyboardInterrupt` inside the ``Live`` block and
    swallows it so the REPL doesn't see a traceback. ``stream_to_console``
    in ``streaming.py`` uses a double-press pattern because it's
    rendering an LLM response that the user might *not* want to abort
    on a stray keypress; a logs-style view is the inverse — one press
    is the canonical "stop" signal.
    """
    console.print(f"[{BOLD_BRAND}]trace {escape(label)}[/]  [{DIM}]Ctrl+C to stop[/]")
    try:
        with (
            patch_stdout(raw=True),
            Live(
                Text(""),
                console=console,
                refresh_per_second=_TRACE_REFRESH_PER_SECOND,
                transient=False,
                vertical_overflow="visible",
            ) as live,
        ):
            # Iterating ``sess`` is what drains the reader queue and
            # appends to ``sess.buffer`` — the loop body only needs the
            # *side effect* of advancing, not the chunk value, so the
            # iteration variable is intentionally discarded.
            # Seeded at 0.0 so the first iteration always renders (any
            # ``time.monotonic() - 0.0`` clears the period); the throttle
            # kicks in from the second iteration onward.
            last_render = 0.0
            pending = False
            for _ in sess:
                now = time.monotonic()
                if now - last_render >= _TRACE_RENDER_PERIOD_S:
                    _render_trace_snapshot(live, sess)
                    last_render = now
                    pending = False
                else:
                    pending = True
            # Final flush: the last chunk(s) may have arrived inside a
            # throttle window; render once after the loop so the user
            # sees the very latest state instead of whatever was on
            # screen at the last gated update.
            if pending:
                _render_trace_snapshot(live, sess)
    except KeyboardInterrupt:
        # kubectl-logs-style: a single Ctrl+C ends the trace and returns
        # to the REPL prompt without propagating a traceback. The
        # ``with sess:`` in the caller still runs and joins the reader
        # thread, so this swallow is safe.
        pass
    if sess.producer_exited:
        # Distinguish "the agent died and we noticed" from "the user
        # asked us to stop" so a long unattended trace doesn't look the
        # same as a Ctrl+C abort.
        console.print(f"[{DIM}]· process exited[/]")
    console.print(f"[{DIM}]· trace ended[/]")


def _cmd_agents_trace(session: ReplSession, console: Console, args: list[str]) -> bool:
    """Live-tail an agent's stdout by pid; see :func:`_render_live_tail`.

    Validates eagerly (``attach()`` raises :class:`AttachUnsupported`
    synchronously on bad pid / unsupported fd type / missing file) so
    we never enter the ``Live`` block on a target we cannot tail.
    """
    if len(args) != 1:
        console.print(f"[{ERROR}]usage:[/] /agents trace <pid>")
        session.mark_latest(ok=False, kind="slash")
        return True
    try:
        pid = int(args[0])
    except ValueError:
        console.print(f"[{ERROR}]invalid pid:[/] {escape(args[0])}")
        session.mark_latest(ok=False, kind="slash")
        return True

    record = AgentRegistry().get(pid)
    label = f"{record.name} (pid {pid})" if record else f"pid {pid}"

    try:
        sess = attach(pid)
    except AttachUnsupported as exc:
        console.print(f"[{ERROR}]cannot trace {escape(label)}:[/] {escape(exc.reason)}")
        session.mark_latest(ok=False, kind="slash")
        return True

    with sess:
        _render_live_tail(console, label, sess)
    return True


def _cmd_agents(session: ReplSession, console: Console, args: list[str]) -> bool:
    if not args:
        return _cmd_agents_list(console)

    sub = args[0].lower().strip()

    if sub == "budget":
        return _cmd_agents_budget(session, console, args[1:])
    if sub == "bus":
        return _cmd_agents_bus(console)
    if sub == "conflicts":
        return _cmd_agents_conflicts(console)

    if sub == "claim":
        return _cmd_agents_claim(session, console, args[1:])

    if sub == "kill":
        return _cmd_agents_kill(session, console, args[1:])

    if sub == "release":
        return _cmd_agents_release(session, console, args[1:])

    if sub == "trace":
        return _cmd_agents_trace(session, console, args[1:])

    console.print(
        f"[{ERROR}]unknown subcommand:[/] {escape(sub)}  "
        "(try [bold]/agents[/bold], [bold]/agents budget[/bold], "
        "[bold]/agents bus[/bold], [bold]/agents claim[/bold], "
        "[bold]/agents conflicts[/bold], [bold]/agents kill[/bold], "
        "[bold]/agents release[/bold], or [bold]/agents trace[/bold])"
    )
    session.mark_latest(ok=False, kind="slash")
    return True


COMMANDS: list[SlashCommand] = [
    SlashCommand(
        "/agents",
        "show registered local AI agents (subcommands: budget, bus, claim, conflicts, kill, "
        "release, trace)",
        _cmd_agents,
        first_arg_completions=_AGENTS_FIRST_ARGS,
    ),
]

__all__ = ["COMMANDS"]
