"""``opensre hermes`` command group: live-tail Hermes logs and dispatch to Telegram.

The ``opensre hermes watch`` command wires the existing detection
backbone (:class:`~app.hermes.HermesAgent`) to the
:class:`~app.hermes.TelegramSink` and blocks until ``SIGINT`` /
``SIGTERM``. Credentials are loaded via
:func:`~app.watch_dog.alarms.load_credentials_from_env`, so the
``TELEGRAM_BOT_TOKEN`` env var must be set; ``--chat-id`` overrides the
``TELEGRAM_DEFAULT_CHAT_ID`` env var when both are present.

This command intentionally does *not* run an OpenSRE investigation by
default. Pass ``--investigate`` (or set ``OPENSRE_HERMES_INVESTIGATE=1``)
to enable the RCA bridge for ``HIGH``/``CRITICAL`` incidents.
"""

from __future__ import annotations

import os
import signal
import threading
from pathlib import Path
from typing import Any

import click

from app.hermes.agent import DEFAULT_LOG_PATH, HermesAgent
from app.hermes.correlating_sink import CorrelatingSink
from app.hermes.correlator import IncidentCorrelator, RouteDestination
from app.hermes.investigation import run_incident_investigation
from app.hermes.sinks import TelegramSink
from app.watch_dog.alarms import AlarmDispatcher, load_credentials_from_env


@click.group(name="hermes")
def hermes_command() -> None:
    """Live-tail Hermes logs and route detected incidents to Telegram."""


@hermes_command.command(name="watch")
@click.option(
    "--log-path",
    type=click.Path(dir_okay=False, path_type=Path),
    default=None,
    help=(
        f"Path to the Hermes log file. Defaults to {DEFAULT_LOG_PATH}. "
        "The file does not need to exist yet — the tailer waits for it to appear."
    ),
)
@click.option(
    "--chat-id",
    "chat_id",
    type=str,
    default=None,
    help=(
        "Telegram chat ID to deliver incidents to. Overrides "
        "TELEGRAM_DEFAULT_CHAT_ID when both are set."
    ),
)
@click.option(
    "--cooldown-seconds",
    type=float,
    default=300.0,
    show_default=True,
    help="Per-incident-fingerprint cooldown before a duplicate is re-sent.",
)
@click.option(
    "--from-start/--from-end",
    default=False,
    show_default=True,
    help=(
        "Replay the existing log contents before live-tailing. Off by default so "
        "starting the watcher does not flood Telegram with backlog."
    ),
)
@click.option(
    "--investigate/--no-investigate",
    "investigate",
    default=None,
    help=(
        "Run an OpenSRE investigation for HIGH/CRITICAL incidents and append "
        "the RCA summary before delivery. Defaults to off; set "
        "OPENSRE_HERMES_INVESTIGATE=1 to enable globally."
    ),
)
@click.option(
    "--correlate/--no-correlate",
    "correlate",
    default=True,
    show_default=True,
    help=(
        "Route classifier output through the IncidentCorrelator (dedup, "
        "severity escalation, rule→destination routing). Disable to pipe "
        "raw incidents straight into the TelegramSink — the legacy behavior."
    ),
)
@click.option(
    "--dedup-window-seconds",
    type=float,
    default=300.0,
    show_default=True,
    help=(
        "Correlator dedup window. Identical fingerprints within this window "
        "are suppressed unless escalation breaks through. Ignored when "
        "--no-correlate is set."
    ),
)
@click.option(
    "--escalation-threshold",
    type=int,
    default=3,
    show_default=True,
    help=(
        "Number of repeat hits within --escalation-window-seconds that "
        "bumps an incident's severity one rung (HIGH→CRITICAL). Must be ≥2. "
        "Ignored when --no-correlate is set."
    ),
)
@click.option(
    "--escalation-window-seconds",
    type=float,
    default=600.0,
    show_default=True,
    help=(
        "Window over which repeat hits are counted for escalation. Ignored "
        "when --no-correlate is set."
    ),
)
def hermes_watch(
    log_path: Path | None,
    chat_id: str | None,
    cooldown_seconds: float,
    from_start: bool,
    investigate: bool | None,
    correlate: bool,
    dedup_window_seconds: float,
    escalation_threshold: int,
    escalation_window_seconds: float,
) -> None:
    """Start the Hermes log watcher and block until interrupted.

    Loads Telegram credentials from the environment, constructs a
    :class:`HermesAgent` wired to a :class:`TelegramSink`, then waits
    for ``SIGINT``/``SIGTERM`` before shutting the agent down cleanly.
    """
    creds = load_credentials_from_env(chat_id_override=chat_id)
    dispatcher = AlarmDispatcher(creds, cooldown_seconds=cooldown_seconds)

    investigate_enabled = _resolve_investigate_flag(investigate)
    bridge = run_incident_investigation if investigate_enabled else None
    telegram_sink = TelegramSink(dispatcher, investigation_bridge=bridge)

    correlator: IncidentCorrelator | None = None
    sink: Any
    if correlate:
        correlator = IncidentCorrelator(
            dedup_window_s=dedup_window_seconds,
            escalation_window_s=escalation_window_seconds,
            escalation_threshold=escalation_threshold,
        )
        # PAGER destinations currently fall back to Telegram with a clear
        # marker — there is no separate pager integration yet. Routing the
        # same sink under both keys keeps escalations visible until a
        # dedicated PagerDuty/Opsgenie sink is added.
        sink = CorrelatingSink(
            correlator=correlator,
            routes={
                RouteDestination.TELEGRAM: telegram_sink,
                RouteDestination.TELEGRAM_WITH_RCA: telegram_sink,
                RouteDestination.PAGER: telegram_sink,
            },
            default_route=telegram_sink,
        )
    else:
        sink = telegram_sink

    resolved_log_path = log_path or DEFAULT_LOG_PATH
    agent = HermesAgent(
        sink=sink,
        log_path=resolved_log_path,
        from_start=from_start,
    )

    stop_event = threading.Event()
    _install_shutdown_handlers(stop_event)

    click.echo(
        f"hermes-watch: tailing {resolved_log_path} "
        f"(cooldown={cooldown_seconds:.0f}s, "
        f"investigate={'on' if investigate_enabled else 'off'}, "
        f"correlate={'on' if correlate else 'off'})"
    )

    agent.start()
    try:
        # Block on the stop_event so SIGINT/SIGTERM wake the main
        # thread immediately. A plain ``thread.join()`` would not
        # respond to signals on its own.
        stop_event.wait()
    finally:
        click.echo("hermes-watch: stopping…")
        agent.stop()
        # Drain in-flight investigation calls (no-op when bridge is
        # disabled). Done after agent.stop() so no new bridge submits
        # can race the shutdown.
        telegram_sink.close()
        if correlator is not None and isinstance(sink, CorrelatingSink):
            snapshot = sink.metrics_snapshot()
            click.echo(
                "hermes-watch: correlator metrics "
                f"delivered={snapshot['delivered']} "
                f"suppressed={snapshot['suppressed']} "
                f"escalated={snapshot['escalated']} "
                f"dropped={snapshot['dropped']} "
                f"unrouted={snapshot['unrouted']} "
                f"sink_errors={snapshot['sink_errors']}"
            )
        click.echo("hermes-watch: stopped.")


def _resolve_investigate_flag(cli_value: bool | None) -> bool:
    """CLI flag wins; otherwise fall back to the env var."""
    if cli_value is not None:
        return cli_value
    env_value = os.getenv("OPENSRE_HERMES_INVESTIGATE", "").strip().lower()
    return env_value in {"1", "true", "yes", "on"}


def _install_shutdown_handlers(stop_event: threading.Event) -> None:
    """Install SIGINT/SIGTERM handlers that set ``stop_event``.

    Click already installs a SIGINT-based ``KeyboardInterrupt`` path,
    but the watcher blocks on a ``threading.Event`` rather than a
    user-input prompt, so we replace it with a handler that wakes the
    event explicitly. SIGTERM gets the same treatment for systemd /
    container shutdowns.
    """

    def _handler(_signum: int, _frame: Any) -> None:
        stop_event.set()

    signal.signal(signal.SIGINT, _handler)
    # SIGTERM is not defined on Windows; guard for portability even
    # though the watcher is a Linux/macOS workflow today.
    sigterm = getattr(signal, "SIGTERM", None)
    if sigterm is not None:
        signal.signal(sigterm, _handler)


__all__ = ["hermes_command"]
