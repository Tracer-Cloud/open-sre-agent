"""``opensre watchdog`` process-threshold monitor."""

from __future__ import annotations

import click
from pydantic import ValidationError

from app.cli.support.errors import OpenSREError
from app.watch_dog.config import WatchdogConfig
from app.watch_dog.runner import run_watchdog


@click.command(name="watchdog")
@click.option(
    "--pid", type=int, default=None, help="PID to monitor. Mutually exclusive with --name."
)
@click.option(
    "--name", "process_name", type=str, default=None, help="Process-name regex to monitor."
)
@click.option("--pick-first", is_flag=True, help="Use the lowest PID when --name matches many.")
@click.option(
    "--max-cpu", type=float, default=None, help="Alarm when CPU percent reaches this value."
)
@click.option(
    "--cpu-window",
    type=str,
    default="30",
    show_default=True,
    help="CPU smoothing window in seconds, or with s/m/h suffix.",
)
@click.option(
    "--max-runtime", type=str, default=None, help="Alarm after runtime exceeds s/m/h duration."
)
@click.option(
    "--max-rss", type=str, default=None, help="Alarm when RSS exceeds bytes or K/M/G/T size."
)
@click.option(
    "--interval", type=float, default=5.0, show_default=True, help="Sample period in seconds."
)
@click.option(
    "--cooldown",
    type=str,
    default="5m",
    show_default=True,
    help="Minimum gap between repeated alarms per threshold.",
)
@click.option("--once", is_flag=True, help="Exit after the first threshold alarm.")
@click.option("--chat-id", type=str, default=None, help="Override TELEGRAM_DEFAULT_CHAT_ID.")
@click.option("--verbose", is_flag=True, help="Print one line per sampled process state.")
def watchdog_command(
    pid: int | None,
    process_name: str | None,
    pick_first: bool,
    max_cpu: float | None,
    cpu_window: str,
    max_runtime: str | None,
    max_rss: str | None,
    interval: float,
    cooldown: str,
    once: bool,
    chat_id: str | None,
    verbose: bool,
) -> None:
    """Monitor a process and send Telegram alarms when thresholds trip."""
    try:
        config = WatchdogConfig.model_validate(
            {
                "pid": pid,
                "name": process_name,
                "pick_first": pick_first,
                "max_cpu": max_cpu,
                "cpu_window": cpu_window,
                "max_runtime": max_runtime,
                "max_rss": max_rss,
                "interval": interval,
                "cooldown": cooldown,
                "once": once,
                "chat_id": chat_id,
                "verbose": verbose,
            }
        )
    except ValidationError as exc:
        raise OpenSREError(
            "Invalid watchdog configuration.",
            suggestion=_validation_suggestion(exc),
        ) from exc

    raise SystemExit(run_watchdog(config))


def _validation_suggestion(exc: ValidationError) -> str:
    first = exc.errors()[0]
    location = ".".join(str(part) for part in first.get("loc", ()) if part != "__root__")
    prefix = f"{location}: " if location else ""
    return f"{prefix}{first.get('msg', 'Check the provided options.')}"
