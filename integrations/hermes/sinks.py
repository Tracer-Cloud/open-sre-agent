"""Incident sinks for the Hermes agent.

The Hermes agent emits :class:`HermesIncident` objects to a pluggable
``IncidentSink`` callable. This module provides the concrete sinks used
in production:

* :class:`TelegramSink` — formats an incident into a human-readable
  Telegram message and routes it through :class:`AlarmDispatcher` so
  duplicate incidents respect the per-fingerprint cooldown.
* :func:`make_telegram_sink` — convenience factory returning an
  :data:`IncidentSink` callable bound to an existing
  :class:`AlarmDispatcher`.

The sink is intentionally *defensive*: any exception raised by Telegram
delivery is logged but does not re-raise, so a delivery bug never
silently disables incident notifications.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Final, Protocol

from integrations.hermes.agent import IncidentSink
from integrations.hermes.incident import HermesIncident, IncidentSeverity, LogRecord


class AlarmDispatcherPort(Protocol):
    """Minimal alarm-dispatch contract this sink depends on.

    Both :class:`integrations.telegram.alarms.AlarmDispatcher` and
    :class:`integrations.rocketchat.alarms.RocketChatAlarmDispatcher` satisfy
    this structurally — the sink stays behind a local protocol (matching
    ``tools.system.watch_dog.monitor.AlarmDispatcherPort``) so it never
    imports a specific provider's dispatcher.
    """

    def dispatch(self, threshold_name: str, message: str) -> bool:
        """Dispatch one alarm; return whether delivery succeeded."""


logger = logging.getLogger(__name__)

# Soft cap on how many raw log records we inline into the Telegram body.
# AlarmDispatcher truncates the final payload at the Telegram 4096 char
# limit, but trimming here keeps the message useful instead of having
# half the records cut off mid-traceback.
_MAX_INLINED_RECORDS: Final[int] = 8
_MAX_RECORD_CHARS: Final[int] = 280

_SEVERITY_EMOJI: Final[dict[IncidentSeverity, str]] = {
    IncidentSeverity.LOW: "🟢",
    IncidentSeverity.MEDIUM: "🟡",
    IncidentSeverity.HIGH: "🟠",
    IncidentSeverity.CRITICAL: "🔴",
}


@dataclass(frozen=True, slots=True)
class TelegramSinkConfig:
    """Optional knobs for :class:`TelegramSink`.

    Defaults match the values used in production. The dataclass is
    frozen so tests can pass a config instance into the sink without
    worrying about cross-test mutation.
    """

    max_inlined_records: int = _MAX_INLINED_RECORDS
    max_record_chars: int = _MAX_RECORD_CHARS


class TelegramSink:
    """Format Hermes incidents and dispatch them to Telegram.

    Parameters
    ----------
    dispatcher:
        Pre-constructed :class:`AlarmDispatcher`. The sink uses
        ``dispatch(threshold_name=incident.fingerprint, message=...)`` so
        duplicate incidents (same fingerprint) are suppressed by the
        dispatcher's cooldown window.
    config:
        Optional :class:`TelegramSinkConfig` overriding inline truncation.
    """

    __slots__ = ("_dispatcher", "_config")

    def __init__(
        self,
        dispatcher: AlarmDispatcherPort,
        *,
        config: TelegramSinkConfig | None = None,
    ) -> None:
        self._dispatcher = dispatcher
        self._config = config if config is not None else TelegramSinkConfig()

    def __call__(self, incident: HermesIncident) -> None:
        """Format the incident and dispatch it. Never raises."""
        try:
            message = self._format_message(incident)
            self._dispatcher.dispatch(incident.fingerprint, message)
        except Exception:
            # The Hermes agent already guards sink exceptions in its own
            # dispatch loop, but logging here gives the operator the
            # incident metadata that the agent's logger does not have.
            logger.exception(
                "telegram sink failed: rule=%s severity=%s fingerprint=%s",
                incident.rule,
                incident.severity.value,
                incident.fingerprint,
            )

    def close(self) -> None:
        """No-op retained so hosts can close sinks uniformly."""

    # ------------------------------------------------------------------
    # Message formatting

    def _format_message(self, incident: HermesIncident) -> str:
        emoji = _SEVERITY_EMOJI.get(incident.severity, "⚠️")
        header = (
            f"{emoji} Hermes incident: {incident.title}\n"
            f"severity: {incident.severity.value.upper()}  "
            f"rule: {incident.rule}\n"
            f"logger: {incident.logger or '<unknown>'}\n"
            f"detected_at: {incident.detected_at.isoformat()}\n"
            f"fingerprint: {incident.fingerprint}"
        )
        if incident.run_id:
            header += f"\nrun_id: {incident.run_id}"

        body_parts: list[str] = [header]

        records_block = self._format_records(incident.records)
        if records_block:
            body_parts.append("recent log records:\n" + records_block)

        return "\n\n".join(body_parts)

    def _format_records(self, records: tuple[LogRecord, ...]) -> str:
        if not records:
            return ""
        inlined = records[: self._config.max_inlined_records]
        omitted = len(records) - len(inlined)
        lines = [_truncate(record.raw, self._config.max_record_chars) for record in inlined]
        if omitted > 0:
            lines.append(f"… ({omitted} more record{'s' if omitted != 1 else ''} omitted)")
        return "\n".join(lines)


def make_telegram_sink(
    dispatcher: AlarmDispatcherPort,
    *,
    config: TelegramSinkConfig | None = None,
) -> IncidentSink:
    """Build an :data:`IncidentSink` callable bound to ``dispatcher``."""
    sink = TelegramSink(dispatcher, config=config)
    return sink


def _truncate(text: str, limit: int) -> str:
    if limit <= 0 or len(text) <= limit:
        return text
    if limit <= 1:
        return text[:limit]
    return text[: limit - 1] + "…"


__all__ = [
    "TelegramSink",
    "TelegramSinkConfig",
    "make_telegram_sink",
]
