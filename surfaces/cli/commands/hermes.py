"""``opensre hermes`` command group: live-tail Hermes logs and dispatch alarms.

The ``opensre hermes watch`` command wires the existing detection
backbone (:class:`~integrations.hermes.HermesAgent`) to a
:class:`~integrations.hermes.TelegramSink` and blocks until ``SIGINT`` /
``SIGTERM``. ``--provider`` selects the delivery channel (``telegram``
default, or ``rocketchat``); credentials are loaded via
:func:`~integrations.telegram.credentials.load_credentials_from_env` or
:func:`~integrations.rocketchat.credentials.load_credentials_from_env`
respectively. ``--chat-id`` overrides the provider's default
chat/channel (``TELEGRAM_DEFAULT_CHAT_ID`` or ``ROCKETCHAT_DEFAULT_CHANNEL``)
when both are present.

"""

from __future__ import annotations

import signal
import threading
from pathlib import Path
from typing import Any

import click

from integrations.hermes import (
    DEFAULT_LOG_PATH,
    AlarmDispatcherPort,
    CorrelatingSink,
    HermesAgent,
    IncidentCorrelator,
    RouteDestination,
    TelegramSink,
)
from integrations.rocketchat import (
    load_credentials_from_env as load_rocketchat_credentials_from_env,
)
from integrations.rocketchat.alarms import RocketChatAlarmDispatcher
from integrations.telegram.alarms import AlarmDispatcher
from integrations.telegram.credentials import load_credentials_from_env


@click.group(name="hermes", invoke_without_command=True)
@click.pass_context
def hermes_command(ctx: click.Context) -> None:
    """Live-tail Hermes logs and route detected incidents to Telegram."""
    if ctx.invoked_subcommand is None:
        click.echo(ctx.get_help())
        ctx.exit(0)


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
    "--provider",
    type=click.Choice(["telegram", "rocketchat"], case_sensitive=False),
    default="telegram",
    show_default=True,
    help="Messaging provider for incident delivery.",
)
@click.option(
    "--chat-id",
    "chat_id",
    type=str,
    default=None,
    help=(
        "Chat/channel to deliver incidents to. Overrides the provider's "
        "default (TELEGRAM_DEFAULT_CHAT_ID or ROCKETCHAT_DEFAULT_CHANNEL) "
        "when both are set."
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
    type=click.IntRange(min=2),
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
    provider: str,
    chat_id: str | None,
    cooldown_seconds: float,
    from_start: bool,
    correlate: bool,
    dedup_window_seconds: float,
    escalation_threshold: int,
    escalation_window_seconds: float,
) -> None:
    """Start the Hermes log watcher and block until interrupted.

    Loads the selected provider's credentials from the environment,
    constructs a :class:`HermesAgent` wired to a :class:`TelegramSink`
    (delivering via Telegram or Rocket.Chat depending on ``--provider``),
    then waits for ``SIGINT``/``SIGTERM`` before shutting the agent down
    cleanly.
    """
    dispatcher: AlarmDispatcherPort
    if provider == "rocketchat":
        rc_creds = load_rocketchat_credentials_from_env(channel_override=chat_id)
        dispatcher = RocketChatAlarmDispatcher(rc_creds, cooldown_seconds=cooldown_seconds)
    else:
        creds = load_credentials_from_env(chat_id_override=chat_id)
        dispatcher = AlarmDispatcher(creds, cooldown_seconds=cooldown_seconds)

    telegram_sink = TelegramSink(dispatcher)

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
        f"(provider={provider}, cooldown={cooldown_seconds:.0f}s, "
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
