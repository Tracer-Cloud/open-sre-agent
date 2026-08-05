"""Foreground watchdog loop and alarm formatting."""

from __future__ import annotations

import html
import socket
import time
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Protocol

import click

from integrations.buzz.alarms import BuzzAlarmDispatcher
from integrations.buzz.credentials import (
    load_credentials_from_env as load_buzz_credentials_from_env,
)
from integrations.rocketchat.alarms import RocketChatAlarmDispatcher
from integrations.rocketchat.credentials import (
    load_credentials_from_env as load_rocketchat_credentials_from_env,
)
from integrations.telegram.alarms import AlarmDispatcher
from integrations.telegram.credentials import load_credentials_from_env
from platform.common.exit_codes import ERROR, SUCCESS
from platform.scheduler.types import Provider
from tools.system.watch_dog.config import WatchdogConfig, WatchdogThreshold
from tools.system.watch_dog.process_monitor import ProcessMonitor, ProcessSample, Sampler


class Dispatcher(Protocol):
    """Minimal alarm dispatcher protocol used by the runner tests."""

    def dispatch(self, threshold_name: str, message: str) -> bool:
        """Dispatch one threshold alarm."""


@dataclass(frozen=True)
class ThresholdBreach:
    """A threshold violation observed for a sample."""

    name: WatchdogThreshold
    limit: float
    observed: float
    window_seconds: float | None = None


def run_watchdog(
    config: WatchdogConfig,
    *,
    sampler: Sampler | None = None,
    dispatcher: Dispatcher | None = None,
    _sleep: Callable[[float], None] = time.sleep,
    _clock: Callable[[], float] = time.monotonic,
) -> int:
    """Run the watchdog loop until target exit, SIGINT, or --once alarm."""
    active_sampler = sampler or ProcessMonitor(config)
    active_dispatcher = dispatcher or _build_dispatcher(config)
    cpu_window = _CpuWindow()

    try:
        while True:
            sample = active_sampler.sample()
            if not sample.alive:
                if config.verbose:
                    click.echo(f"watchdog: target exited pid={sample.pid}")
                return SUCCESS

            if not sample.accessible:
                # The process is still running but denied this read
                # (transient psutil.AccessDenied). Retry next tick rather
                # than mis-reporting an exit or alarming on zeroed metrics.
                if config.verbose:
                    click.echo(
                        f"watchdog: sample inaccessible pid={sample.pid} "
                        "(permission denied); retrying"
                    )
                _sleep(config.interval)
                continue

            now = _clock()
            breaches = _evaluate_thresholds(config, sample, cpu_window=cpu_window, now=now)
            if config.verbose:
                click.echo(_format_sample_log(sample, breaches))

            for breach in breaches:
                active_dispatcher.dispatch(
                    breach.name.value,
                    _format_alarm_message(sample, breach, provider=config.provider),
                )

            if breaches and config.once:
                return ERROR

            _sleep(config.interval)
    except KeyboardInterrupt:
        return SUCCESS


def _build_dispatcher(config: WatchdogConfig) -> Dispatcher:
    if config.provider == Provider.ROCKETCHAT:
        rc_creds = load_rocketchat_credentials_from_env(channel_override=config.chat_id)
        return RocketChatAlarmDispatcher(rc_creds, cooldown_seconds=config.cooldown)
    if config.provider == Provider.BUZZ:
        buzz_creds = load_buzz_credentials_from_env(channel_override=config.chat_id)
        return BuzzAlarmDispatcher(buzz_creds, cooldown_seconds=config.cooldown)
    creds = load_credentials_from_env(chat_id_override=config.chat_id)
    return AlarmDispatcher(creds, cooldown_seconds=config.cooldown, parse_mode="HTML")


def _evaluate_thresholds(
    config: WatchdogConfig,
    sample: ProcessSample,
    *,
    cpu_window: _CpuWindow,
    now: float,
) -> tuple[ThresholdBreach, ...]:
    breaches: list[ThresholdBreach] = []

    if config.max_cpu is not None:
        observed_cpu = cpu_window.add(now, sample.cpu_percent, window_seconds=config.cpu_window)
        if observed_cpu >= config.max_cpu:
            breaches.append(
                ThresholdBreach(
                    name=WatchdogThreshold.MAX_CPU,
                    limit=config.max_cpu,
                    observed=observed_cpu,
                    window_seconds=config.cpu_window,
                )
            )

    if config.max_runtime is not None and sample.runtime_seconds >= config.max_runtime:
        breaches.append(
            ThresholdBreach(
                name=WatchdogThreshold.MAX_RUNTIME,
                limit=config.max_runtime,
                observed=sample.runtime_seconds,
            )
        )

    if config.max_rss is not None and sample.rss_bytes >= config.max_rss:
        breaches.append(
            ThresholdBreach(
                name=WatchdogThreshold.MAX_RSS,
                limit=float(config.max_rss),
                observed=float(sample.rss_bytes),
            )
        )

    return tuple(breaches)


class _CpuWindow:
    """Rolling CPU average over a time window."""

    def __init__(self) -> None:
        self._samples: deque[tuple[float, float]] = deque()

    def add(self, now: float, value: float, *, window_seconds: float) -> float:
        self._samples.append((now, value))
        oldest_allowed = now - window_seconds
        while len(self._samples) > 1 and self._samples[0][0] < oldest_allowed:
            self._samples.popleft()
        return sum(cpu for _, cpu in self._samples) / len(self._samples)


def _format_sample_log(
    sample: ProcessSample,
    breaches: tuple[ThresholdBreach, ...],
) -> str:
    status = "alarm" if breaches else "ok"
    return (
        "watchdog: "
        f"status={status} pid={sample.pid} name={sample.name} "
        f"cpu={sample.cpu_percent:.1f}% rss={_format_bytes(sample.rss_bytes)} "
        f"runtime={_format_duration(sample.runtime_seconds)}"
    )


def _alarm_started_and_command(sample: ProcessSample) -> tuple[str, str]:
    started = "-"
    if sample.started_at is not None:
        started = datetime.fromtimestamp(sample.started_at, tz=UTC).isoformat()
        started = started.replace("+00:00", "Z")

    command = sample.command or "-"
    if len(command) > 180:
        command = f"{command[:177]}..."
    return started, command


def _format_alarm_message(
    sample: ProcessSample, breach: ThresholdBreach, *, provider: Provider
) -> str:
    """Format the alarm body for the delivering provider.

    Telegram renders HTML (``parse_mode="HTML"``); Rocket.Chat renders
    Markdown and displays literal ``<b>``/``<code>`` tags as text, so each
    provider gets its own markup around the same fields rather than sending
    one format to both.
    """
    if provider == Provider.ROCKETCHAT:
        return _format_alarm_message_markdown(sample, breach)
    return _format_alarm_message_html(sample, breach)


def _format_alarm_message_html(sample: ProcessSample, breach: ThresholdBreach) -> str:
    started, command = _alarm_started_and_command(sample)

    return "\n".join(
        [
            "<b>🚨 OpenSRE Watchdog Alarm</b>",
            f"<b>host</b>       <code>{html.escape(socket.gethostname())}</code>",
            f"<b>pid</b>        <code>{sample.pid}</code>  "
            f"(<code>{html.escape(sample.name or '-')}</code>)",
            f"<b>cmd</b>        <code>{html.escape(command)}</code>",
            f"<b>threshold</b>  <code>{html.escape(_format_threshold_breach(breach))}</code>",
            f"<b>runtime</b>    <code>{html.escape(_format_duration(sample.runtime_seconds))}</code>",
            f"<b>started</b>    <code>{html.escape(started)}</code>",
        ]
    )


def _md_code(value: str) -> str:
    """Wrap *value* in a backtick code span, neutralizing inner backticks.

    A literal backtick in the value (e.g. a shell command using command
    substitution like `` echo `date` ``) would otherwise terminate the code
    span early and render the rest of the field as unformatted text. Swap it
    for the visually similar U+02CB MODIFIER LETTER GRAVE ACCENT, matching
    how the HTML formatter's html.escape() neutralizes markup characters in
    the same fields.
    """
    return f"`{value.replace('`', 'ˋ')}`"


def _format_alarm_message_markdown(sample: ProcessSample, breach: ThresholdBreach) -> str:
    started, command = _alarm_started_and_command(sample)

    return "\n".join(
        [
            "**🚨 OpenSRE Watchdog Alarm**",
            f"**host**       {_md_code(socket.gethostname())}",
            f"**pid**        {_md_code(str(sample.pid))}  ({_md_code(sample.name or '-')})",
            f"**cmd**        {_md_code(command)}",
            f"**threshold**  {_md_code(_format_threshold_breach(breach))}",
            f"**runtime**    {_md_code(_format_duration(sample.runtime_seconds))}",
            f"**started**    {_md_code(started)}",
        ]
    )


def _format_threshold_breach(breach: ThresholdBreach) -> str:
    if breach.name == WatchdogThreshold.MAX_CPU:
        window = f"  window={_format_duration(breach.window_seconds or 0)}"
        return f"max_cpu  limit={breach.limit:.1f}%  observed={breach.observed:.1f}%{window}"
    if breach.name == WatchdogThreshold.MAX_RUNTIME:
        return (
            "max_runtime  "
            f"limit={_format_duration(breach.limit)}  "
            f"observed={_format_duration(breach.observed)}"
        )
    if breach.name == WatchdogThreshold.MAX_RSS:
        return (
            "max_rss  "
            f"limit={_format_bytes(breach.limit)}  "
            f"observed={_format_bytes(breach.observed)}"
        )
    return f"{breach.name}  limit={breach.limit}  observed={breach.observed}"


def _format_duration(seconds: float) -> str:
    remaining = max(0, int(round(seconds)))
    hours, remaining = divmod(remaining, 3600)
    minutes, secs = divmod(remaining, 60)
    if hours:
        return f"{hours}h{minutes:02d}m{secs:02d}s"
    if minutes:
        return f"{minutes}m{secs:02d}s"
    return f"{secs}s"


def _format_bytes(value: float) -> str:
    amount = float(value)
    for unit in ("B", "KiB", "MiB", "GiB", "TiB"):
        if abs(amount) < 1024.0 or unit == "TiB":
            if unit == "B":
                return f"{int(amount)}B"
            return f"{amount:.1f}{unit}"
        amount /= 1024.0
    return f"{amount:.1f}TiB"
