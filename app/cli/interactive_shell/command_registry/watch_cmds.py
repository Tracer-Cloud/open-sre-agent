"""Slash commands: /watch, /watches, /unwatch."""

from __future__ import annotations

import os
import re
import threading
from dataclasses import dataclass
from datetime import UTC, datetime

from rich.console import Console
from rich.markup import escape

from app.agents.probe import pid_exists
from app.cli.interactive_shell.command_registry.types import ExecutionTier, SlashCommand
from app.cli.interactive_shell.runtime import ReplSession, TaskKind, TaskRecord, TaskStatus
from app.cli.interactive_shell.ui import (
    BOLD_BRAND,
    DIM,
    ERROR,
    HIGHLIGHT,
    WARNING,
    repl_table,
)
from app.cli.support.errors import OpenSREError
from app.watch_dog.alarms import AlarmDispatcher, load_credentials_from_env
from app.watch_dog.monitor import start_watchdog_daemon_thread

_PID_IN_COMMAND_RE = re.compile(r"pid=(\d+)")
_CONSOLE_PRINT_LOCK = threading.Lock()


@dataclass(frozen=True)
class WatchdogStartSpec:
    pid: int
    max_cpu: float | None = None
    max_runtime_seconds: float | None = None
    max_rss_mib: float | None = None
    cooldown_seconds: float = 300.0
    interval_seconds: float = 2.0
    once: bool = False


def _parse_duration_seconds(raw: str) -> float | None:
    text = raw.strip().lower()
    if not text:
        return None
    mult = 1.0
    if text.endswith("h"):
        mult = 3600.0
        text = text[:-1]
    elif text.endswith("m") and not text.endswith("ms"):
        mult = 60.0
        text = text[:-1]
    elif text.endswith("s"):
        mult = 1.0
        text = text[:-1]
    try:
        return float(text) * mult
    except ValueError:
        return None


def _parse_mib(raw: str) -> float | None:
    text = raw.strip().lower()
    if not text:
        return None
    mult = 1.0
    if text.endswith("gib") or text.endswith("gb") or text.endswith("g"):
        mult = 1024.0
        for suffix in ("gib", "gb", "g"):
            if text.endswith(suffix):
                text = text[: -len(suffix)]
                break
    elif text.endswith("mib") or text.endswith("mb") or text.endswith("m"):
        mult = 1.0
        for suffix in ("mib", "mb", "m"):
            if text.endswith(suffix):
                text = text[: -len(suffix)]
                break
    try:
        return float(text) * mult
    except ValueError:
        return None


def parse_watch_argv(argv: list[str]) -> WatchdogStartSpec | str:
    """Parse ``/watch`` arguments (tokens after the command name)."""
    if not argv:
        return (
            f"[{ERROR}]usage:[/] /watch <pid> [--max-cpu N] [--max-runtime D] [--max-rss S] "
            f"[--cooldown D] [--interval N] [--once]"
        )
    if argv[0].startswith("-"):
        return f"[{ERROR}]usage:[/] /watch <pid> ...  — the process id must come first"

    try:
        pid = int(argv[0], 10)
    except ValueError:
        return f"[{ERROR}]invalid pid:[/] {escape(argv[0])}"
    if pid <= 0:
        return f"[{ERROR}]pid must be a positive integer:[/] {pid}"

    max_cpu: float | None = None
    max_runtime_seconds: float | None = None
    max_rss_mib: float | None = None
    cooldown_seconds = 300.0
    interval_seconds = 2.0
    once = False

    i = 1
    while i < len(argv):
        token = argv[i]
        if token == "--once":
            once = True
            i += 1
            continue
        if not token.startswith("--"):
            return f"[{ERROR}]unexpected argument:[/] {escape(token)}"
        if i + 1 >= len(argv):
            return f"[{ERROR}]missing value for[/] {escape(token)}"
        value = argv[i + 1]
        i += 2
        if token == "--max-cpu":
            try:
                pct = float(value)
            except ValueError:
                return f"[{ERROR}]invalid --max-cpu:[/] {escape(value)}"
            max_supported_cpu = 100.0 * float(max(1, os.cpu_count() or 1))
            if pct <= 0 or pct > max_supported_cpu:
                return f"[{ERROR}]--max-cpu must be between 0 and {max_supported_cpu:g}:[/] {pct}"
            max_cpu = pct
        elif token == "--max-runtime":
            seconds = _parse_duration_seconds(value)
            if seconds is None or seconds <= 0:
                return f"[{ERROR}]invalid --max-runtime:[/] {escape(value)}"
            max_runtime_seconds = seconds
        elif token == "--max-rss":
            mib = _parse_mib(value)
            if mib is None or mib <= 0:
                return f"[{ERROR}]invalid --max-rss:[/] {escape(value)}"
            max_rss_mib = mib
        elif token == "--cooldown":
            seconds = _parse_duration_seconds(value)
            if seconds is None or seconds <= 0:
                return f"[{ERROR}]invalid --cooldown:[/] {escape(value)}"
            cooldown_seconds = seconds
        elif token == "--interval":
            seconds = _parse_duration_seconds(value)
            if seconds is None or seconds <= 0:
                return f"[{ERROR}]invalid --interval:[/] {escape(value)}"
            interval_seconds = seconds
        else:
            return f"[{ERROR}]unknown flag:[/] {escape(token)}"

    return WatchdogStartSpec(
        pid=pid,
        max_cpu=max_cpu,
        max_runtime_seconds=max_runtime_seconds,
        max_rss_mib=max_rss_mib,
        cooldown_seconds=cooldown_seconds,
        interval_seconds=interval_seconds,
        once=once,
    )


def _watch_command_summary(spec: WatchdogStartSpec) -> str:
    parts = [f"watchdog pid={spec.pid}"]
    if spec.max_cpu is not None:
        parts.append(f"max_cpu={spec.max_cpu:g}%")
    if spec.max_runtime_seconds is not None:
        parts.append(f"max_runtime={spec.max_runtime_seconds:g}s")
    if spec.max_rss_mib is not None:
        parts.append(f"max_rss={spec.max_rss_mib:g}MiB")
    parts.append(f"cooldown={spec.cooldown_seconds:g}s")
    parts.append(f"interval={spec.interval_seconds:g}s")
    if spec.once:
        parts.append("once")
    return " ".join(parts)


def _watched_pid_from_task(task: TaskRecord) -> str:
    if task.command:
        match = _PID_IN_COMMAND_RE.search(task.command)
        if match:
            return match.group(1)
    return "—"


def _cmd_watch(session: ReplSession, console: Console, args: list[str]) -> bool:
    parsed = parse_watch_argv(args)
    if isinstance(parsed, str):
        console.print(parsed)
        return True

    if not pid_exists(parsed.pid):
        console.print(f"[{ERROR}]no such process:[/] pid {parsed.pid}")
        return True

    try:
        creds = load_credentials_from_env()
    except OpenSREError as exc:
        console.print(f"[{ERROR}]{escape(str(exc))}[/]")
        return True

    summary = _watch_command_summary(parsed)
    task = session.task_registry.create(TaskKind.WATCHDOG, command=summary)
    task.mark_running()
    task.attach_pid(parsed.pid)
    dispatcher = AlarmDispatcher(creds, cooldown_seconds=parsed.cooldown_seconds)

    def _on_alarm(threshold: str, detail: str) -> None:
        with _CONSOLE_PRINT_LOCK:
            console.print(
                f"[task {escape(task.task_id)}] alarm fired: {escape(threshold)} "
                f"{escape(detail)} (telegram delivered)"
            )

    start_watchdog_daemon_thread(
        task=task,
        watched_pid=parsed.pid,
        interval_seconds=parsed.interval_seconds,
        max_cpu=parsed.max_cpu,
        max_runtime_seconds=parsed.max_runtime_seconds,
        max_rss_mib=parsed.max_rss_mib,
        once=parsed.once,
        dispatcher=dispatcher,
        on_alarm=_on_alarm,
    )
    console.print(f"[{DIM}]task[/] [bold]{escape(task.task_id)}[/bold] [{DIM}]started.[/]")
    return True


def _cmd_watches(session: ReplSession, console: Console, _args: list[str]) -> bool:
    rows = [t for t in session.task_registry.list_recent(n=100) if t.kind == TaskKind.WATCHDOG]
    if not rows:
        console.print(f"[{DIM}]no watchdog tasks in this session.[/]")
        return True

    table = repl_table(title="Watchdogs", title_style=BOLD_BRAND)
    table.add_column("id", style="bold")
    table.add_column("pid", justify="right")
    table.add_column("kind")
    table.add_column("status")
    table.add_column("started", style=DIM)
    table.add_column("thresholds", overflow="fold")
    table.add_column("last sample", style=DIM, overflow="fold")

    status_style = {
        TaskStatus.RUNNING: WARNING,
        TaskStatus.COMPLETED: HIGHLIGHT,
        TaskStatus.CANCELLED: WARNING,
        TaskStatus.FAILED: ERROR,
        TaskStatus.PENDING: DIM,
    }
    for task in rows:
        st = status_style.get(task.status, DIM)
        started = datetime.fromtimestamp(task.started_at, tz=UTC).strftime("%Y-%m-%d %H:%M:%S UTC")
        thresholds = task.command or "—"
        sample = task.progress or "—"
        table.add_row(
            task.task_id,
            _watched_pid_from_task(task),
            TaskKind.WATCHDOG.value,
            f"[{st}]{task.status.value}[/]",
            started,
            escape(thresholds),
            escape(sample),
        )
    console.print(table)
    return True


def _validate_unwatch_args(args: list[str]) -> str | None:
    if not args:
        return f"[{ERROR}]usage:[/] /unwatch <task_id>  — use [{HIGHLIGHT}]/watches[/] to list ids"
    return None


def _cmd_unwatch(session: ReplSession, console: Console, args: list[str]) -> bool:
    needle = args[0]
    candidates = session.task_registry.candidates(needle)
    if not candidates:
        console.print(f"[{ERROR}]no task matches id:[/] {escape(needle)}")
        return True
    if len(candidates) > 1:
        console.print(
            f"[{ERROR}]ambiguous id prefix:[/] {escape(needle)} "
            f"[{DIM}]({len(candidates)} matches — use a longer prefix)[/]"
        )
        return True

    task = candidates[0]
    if task.kind != TaskKind.WATCHDOG:
        console.print(
            f"[{ERROR}]task {escape(task.task_id)} is not a watchdog "
            f"(kind: {escape(task.kind.value)}); use /cancel instead.[/]"
        )
        return True
    if task.status != TaskStatus.RUNNING:
        console.print(
            f"[{DIM}]task {escape(task.task_id)} already finished (status: {task.status.value}).[/]"
        )
        return True

    task.request_cancel()
    console.print(
        f"[{HIGHLIGHT}]stop requested[/] "
        f"[{DIM}]for watchdog {escape(task.task_id)}.[/] "
        f"[{DIM}]use[/] [{HIGHLIGHT}]/watches[/] [{DIM}]to confirm status.[/]"
    )
    return True


def _validate_watch_args(args: list[str]) -> str | None:
    if not args:
        return (
            f"[{ERROR}]usage:[/] /watch <pid> [--max-cpu N] [--max-runtime D] [--max-rss S] "
            f"[--cooldown D] [--interval N] [--once]"
        )
    return None


COMMANDS: list[SlashCommand] = [
    SlashCommand(
        "/watch",
        "watch a process PID in the background; alarms go to Telegram",
        _cmd_watch,
        execution_tier=ExecutionTier.ELEVATED,
        validate_args=_validate_watch_args,
    ),
    SlashCommand(
        "/watches",
        "list watchdog background tasks with last resource sample",
        _cmd_watches,
    ),
    SlashCommand(
        "/unwatch",
        "cancel a running watchdog task by id ('/unwatch <task_id>' — see /watches)",
        _cmd_unwatch,
        execution_tier=ExecutionTier.ELEVATED,
        validate_args=_validate_unwatch_args,
    ),
]

__all__ = ["COMMANDS", "WatchdogStartSpec", "parse_watch_argv"]
