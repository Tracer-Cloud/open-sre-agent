"""WhatsApp sink for the Hermes agent.

Mirrors :class:`~app.hermes.sinks.TelegramSink` but dispatches via Meta's
WhatsApp Cloud API instead of Telegram Bot API.
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable
from concurrent.futures import CancelledError as FutureCancelledError
from concurrent.futures import Future, ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeoutError
from dataclasses import dataclass
from typing import Final

from app.hermes.agent import IncidentSink
from app.hermes.incident import HermesIncident, IncidentSeverity, LogRecord
from app.watch_dog.whatsapp_alarms import WhatsAppAlarmDispatcher

logger = logging.getLogger(__name__)

_INVESTIGATION_SEVERITIES: Final[frozenset[IncidentSeverity]] = frozenset(
    {IncidentSeverity.HIGH, IncidentSeverity.CRITICAL}
)

_MAX_INLINED_RECORDS: Final[int] = 8
_MAX_RECORD_CHARS: Final[int] = 280
_MAX_SUMMARY_CHARS: Final[int] = 1200
_DEFAULT_BRIDGE_WORKERS: Final[int] = 2
_DEFAULT_BRIDGE_TIMEOUT_S: Final[float] = 45.0

_SEVERITY_EMOJI: Final[dict[IncidentSeverity, str]] = {
    IncidentSeverity.LOW: "🟢",
    IncidentSeverity.MEDIUM: "🟡",
    IncidentSeverity.HIGH: "🟠",
    IncidentSeverity.CRITICAL: "🔴",
}

InvestigationBridge = Callable[[HermesIncident], str | None]


@dataclass(frozen=True, slots=True)
class WhatsAppSinkConfig:
    """Optional knobs for :class:`WhatsAppSink`."""

    max_inlined_records: int = _MAX_INLINED_RECORDS
    max_record_chars: int = _MAX_RECORD_CHARS
    max_summary_chars: int = _MAX_SUMMARY_CHARS
    bridge_timeout_s: float = _DEFAULT_BRIDGE_TIMEOUT_S
    bridge_workers: int = _DEFAULT_BRIDGE_WORKERS
    bridge_run_inline: bool = False


class WhatsAppSink:
    """Format Hermes incidents and dispatch them to WhatsApp.

    Parameters
    ----------
    dispatcher:
        Pre-constructed :class:`WhatsAppAlarmDispatcher`. The sink uses
        ``dispatch(threshold_name=incident.fingerprint, message=...)`` so
        duplicate incidents (same fingerprint) are suppressed by the
        dispatcher's cooldown window.
    investigation_bridge:
        Optional callable invoked for ``HIGH``/``CRITICAL`` incidents.
        The call runs in a bounded thread pool with a timeout so the
        agent's polling thread is never blocked for more than
        ``bridge_timeout_s`` seconds.
    config:
        Optional :class:`WhatsAppSinkConfig` overriding inline
        truncation, bridge timeout, and pool size.
    """

    __slots__ = (
        "_dispatcher",
        "_investigation_bridge",
        "_config",
        "_bridge_executor",
        "_bridge_shutdown",
        "_bridge_lock",
    )

    def __init__(
        self,
        dispatcher: WhatsAppAlarmDispatcher,
        *,
        investigation_bridge: InvestigationBridge | None = None,
        config: WhatsAppSinkConfig | None = None,
    ) -> None:
        self._dispatcher = dispatcher
        self._investigation_bridge = investigation_bridge
        self._config = config if config is not None else WhatsAppSinkConfig()
        self._bridge_executor: ThreadPoolExecutor | None = None
        self._bridge_shutdown = False
        self._bridge_lock = threading.Lock()
        if investigation_bridge is not None and not self._config.bridge_run_inline:
            self._bridge_executor = ThreadPoolExecutor(
                max_workers=max(1, self._config.bridge_workers),
                thread_name_prefix="hermes-whatsapp-bridge",
            )

    def __call__(self, incident: HermesIncident) -> None:
        """Format the incident and dispatch it. Never raises."""
        try:
            investigation = self._maybe_investigate(incident)
            message = self._format_message(incident, investigation=investigation)
            self._dispatcher.dispatch(incident.fingerprint, message)
        except Exception:
            logger.exception(
                "whatsapp sink failed: rule=%s severity=%s fingerprint=%s",
                incident.rule,
                incident.severity.value,
                incident.fingerprint,
            )

    def close(self) -> None:
        """Shut down the bridge executor without blocking the caller."""
        with self._bridge_lock:
            self._bridge_shutdown = True
            executor = self._bridge_executor
            if executor is not None:
                executor.shutdown(wait=False, cancel_futures=True)
                self._bridge_executor = None

    # ------------------------------------------------------------------
    # Investigation bridge

    def _maybe_investigate(self, incident: HermesIncident) -> _InvestigationResult:
        bridge = self._investigation_bridge
        if bridge is None:
            return _InvestigationResult.not_attempted()
        if incident.severity not in _INVESTIGATION_SEVERITIES:
            return _InvestigationResult.not_attempted()
        if self._bridge_shutdown:
            return _InvestigationResult.sink_closed()
        if self._config.bridge_run_inline:
            return self._run_bridge_inline(bridge, incident)
        if self._bridge_executor is not None:
            return self._run_bridge_in_pool(bridge, incident)
        return _InvestigationResult.sink_closed()

    def _run_bridge_inline(
        self, bridge: InvestigationBridge, incident: HermesIncident
    ) -> _InvestigationResult:
        try:
            summary = bridge(incident)
        except Exception:
            logger.warning(
                "hermes whatsapp investigation bridge raised: rule=%s fingerprint=%s",
                incident.rule,
                incident.fingerprint,
                exc_info=True,
            )
            return _InvestigationResult.failed()
        return self._coerce_summary(summary)

    def _run_bridge_in_pool(
        self, bridge: InvestigationBridge, incident: HermesIncident
    ) -> _InvestigationResult:
        with self._bridge_lock:
            if self._bridge_shutdown:
                return _InvestigationResult.sink_closed()
            executor = self._bridge_executor
            if executor is None:
                return _InvestigationResult.sink_closed()
            try:
                future: Future[str | None] = executor.submit(bridge, incident)
            except RuntimeError:
                return _InvestigationResult.sink_closed()

        timeout = self._config.bridge_timeout_s
        try:
            summary = future.result(timeout=timeout)
        except FutureTimeoutError:
            logger.warning(
                "hermes whatsapp investigation bridge timed out after %.1fs: rule=%s fingerprint=%s",
                timeout,
                incident.rule,
                incident.fingerprint,
            )
            return _InvestigationResult.timed_out(timeout)
        except FutureCancelledError:
            return _InvestigationResult.sink_closed()
        except Exception:
            logger.warning(
                "hermes whatsapp investigation bridge raised: rule=%s fingerprint=%s",
                incident.rule,
                incident.fingerprint,
                exc_info=True,
            )
            return _InvestigationResult.failed()
        return self._coerce_summary(summary)

    def _coerce_summary(self, summary: str | None) -> _InvestigationResult:
        if not summary:
            return _InvestigationResult.empty()
        return _InvestigationResult.success(
            _truncate(summary.strip(), self._config.max_summary_chars)
        )

    # ------------------------------------------------------------------
    # Message formatting

    def _format_message(
        self,
        incident: HermesIncident,
        *,
        investigation: _InvestigationResult,
    ) -> str:
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

        investigation_block = investigation.render(incident.severity)
        if investigation_block:
            body_parts.append(investigation_block)

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


@dataclass(frozen=True, slots=True)
class _InvestigationResult:
    """Internal value type carrying the outcome of a bridge call."""

    state: str
    summary: str | None = None
    timeout_s: float | None = None

    @classmethod
    def not_attempted(cls) -> _InvestigationResult:
        return cls(state="not_attempted")

    @classmethod
    def success(cls, summary: str) -> _InvestigationResult:
        return cls(state="success", summary=summary)

    @classmethod
    def empty(cls) -> _InvestigationResult:
        return cls(state="empty")

    @classmethod
    def failed(cls) -> _InvestigationResult:
        return cls(state="failed")

    @classmethod
    def timed_out(cls, timeout_s: float) -> _InvestigationResult:
        return cls(state="timed_out", timeout_s=timeout_s)

    @classmethod
    def sink_closed(cls) -> _InvestigationResult:
        return cls(state="sink_closed")

    def render(self, severity: IncidentSeverity) -> str:
        if self.state == "sink_closed":
            return "investigation: skipped (Hermes sink closed — notification only)"
        if self.state == "success" and self.summary is not None:
            return "investigation summary:\n" + self.summary
        if self.state == "empty":
            return "investigation: attempted (no summary produced)"
        if self.state == "failed":
            return "investigation: attempted (failed — see server logs)"
        if self.state == "timed_out" and self.timeout_s is not None:
            return (
                f"investigation: attempted (timed out after "
                f"{self.timeout_s:.1f}s — see server logs)"
            )
        if severity == IncidentSeverity.MEDIUM:
            return "note: warning-burst severity — notify only, no investigation run."
        return ""


def make_whatsapp_sink(
    dispatcher: WhatsAppAlarmDispatcher,
    *,
    investigation_bridge: InvestigationBridge | None = None,
    config: WhatsAppSinkConfig | None = None,
) -> IncidentSink:
    """Build an :data:`IncidentSink` callable bound to ``dispatcher``."""
    sink = WhatsAppSink(
        dispatcher,
        investigation_bridge=investigation_bridge,
        config=config,
    )
    return sink


def _truncate(text: str, limit: int) -> str:
    if limit <= 0 or len(text) <= limit:
        return text
    if limit <= 1:
        return text[:limit]
    return text[: limit - 1] + "…"


__all__ = [
    "InvestigationBridge",
    "WhatsAppSink",
    "WhatsAppSinkConfig",
    "make_whatsapp_sink",
]
