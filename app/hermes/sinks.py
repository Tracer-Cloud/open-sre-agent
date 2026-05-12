"""Incident sinks for the Hermes agent.

The Hermes agent emits :class:`HermesIncident` objects to a pluggable
``IncidentSink`` callable. This module provides the concrete sinks used
in production:

* :class:`TelegramSink` — formats an incident into a human-readable
  Telegram message and routes it through :class:`AlarmDispatcher` so
  duplicate incidents respect the per-fingerprint cooldown. For
  ``HIGH``/``CRITICAL`` incidents it can optionally trigger the OpenSRE
  investigation pipeline and append the resulting root-cause summary to
  the Telegram message before delivery.
* :func:`make_telegram_sink` — convenience factory returning an
  :data:`IncidentSink` callable bound to an existing
  :class:`AlarmDispatcher` (and optional investigation bridge).

The sink is intentionally *defensive*: any exception raised by the
investigation bridge or by Telegram delivery is logged but does not
re-raise. A buggy bridge must never silently disable incident
notifications.

Investigation bridge execution model
------------------------------------

Investigation calls (LLM round-trips through LangGraph) can take 30+
seconds. To keep the agent's polling thread responsive — so the
``FileTailer`` keeps reading and the classifier keeps observing during
an investigation — bridge calls are dispatched to a bounded thread pool
and waited on with a configurable timeout. When the investigation
completes within budget its summary is appended to the Telegram body;
otherwise the sink falls back to a clearly-marked notification so the
operator can distinguish:

* **no bridge configured** → no investigation section
* **bridge attempted, returned None** → ``investigation: attempted (no
  summary produced)``
* **bridge attempted, raised** → ``investigation: attempted (failed —
  see server logs)``
* **bridge attempted, timed out** → ``investigation: attempted (timed
  out after N.Ns — see server logs)``
* **bridge attempted, returned summary** → ``investigation summary:`` block
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
from app.watch_dog.alarms import AlarmDispatcher

logger = logging.getLogger(__name__)

# Severities that trigger a full RCA investigation. MEDIUM (warning
# bursts) intentionally short-circuits to a lighter-weight notification:
# bursts are noisy and the marginal investigation rarely surfaces a true
# root cause for them.
_INVESTIGATION_SEVERITIES: Final[frozenset[IncidentSeverity]] = frozenset(
    {IncidentSeverity.HIGH, IncidentSeverity.CRITICAL}
)

# Soft cap on how many raw log records we inline into the Telegram body.
# AlarmDispatcher truncates the final payload at the Telegram 4096 char
# limit, but trimming here keeps the message useful instead of having
# half the records cut off mid-traceback.
_MAX_INLINED_RECORDS: Final[int] = 8
_MAX_RECORD_CHARS: Final[int] = 280
_MAX_SUMMARY_CHARS: Final[int] = 1200

# Default thread-pool worker count. The bridge is I/O-bound (LLM
# round-trips) so a small pool is enough; we keep this conservative so a
# burst of HIGH/CRITICAL incidents doesn't fan out into dozens of
# concurrent LLM calls.
_DEFAULT_BRIDGE_WORKERS: Final[int] = 2

# How long the sink waits for an in-flight investigation before giving
# up and falling back to a timeout notice. Tuned to be slightly larger
# than a typical LangGraph investigation but well under the
# AlarmDispatcher cooldown (300s default) so a retry on the next
# matching incident gets a fresh shot.
_DEFAULT_BRIDGE_TIMEOUT_S: Final[float] = 45.0

_SEVERITY_EMOJI: Final[dict[IncidentSeverity, str]] = {
    IncidentSeverity.LOW: "🟢",
    IncidentSeverity.MEDIUM: "🟡",
    IncidentSeverity.HIGH: "🟠",
    IncidentSeverity.CRITICAL: "🔴",
}


# An investigation bridge is any callable that, given an incident,
# returns a human-readable RCA summary (or ``None`` if it could not
# produce one). Implementations typically wrap ``run_investigation`` and
# extract ``state["summary"]``/``state["root_cause"]``. Returning
# ``None`` rather than raising is the documented contract — the sink
# treats exceptions and ``None`` distinctly (different operator-visible
# markers) and logs the former at WARNING.
InvestigationBridge = Callable[[HermesIncident], str | None]


@dataclass(frozen=True, slots=True)
class TelegramSinkConfig:
    """Optional knobs for :class:`TelegramSink`.

    Defaults match the values used in production. The dataclass is
    frozen so tests can pass a config instance into the sink without
    worrying about cross-test mutation.
    """

    max_inlined_records: int = _MAX_INLINED_RECORDS
    max_record_chars: int = _MAX_RECORD_CHARS
    max_summary_chars: int = _MAX_SUMMARY_CHARS
    bridge_timeout_s: float = _DEFAULT_BRIDGE_TIMEOUT_S
    bridge_workers: int = _DEFAULT_BRIDGE_WORKERS
    # Run bridge synchronously on the calling thread instead of offloading
    # to the executor. Used by unit tests that want deterministic
    # bridge-call ordering without spinning up a pool.
    bridge_run_inline: bool = False


class TelegramSink:
    """Format Hermes incidents and dispatch them to Telegram.

    Parameters
    ----------
    dispatcher:
        Pre-constructed :class:`AlarmDispatcher`. The sink uses
        ``dispatch(threshold_name=incident.fingerprint, message=...)`` so
        duplicate incidents (same fingerprint) are suppressed by the
        dispatcher's cooldown window.
    investigation_bridge:
        Optional callable invoked for ``HIGH``/``CRITICAL`` incidents.
        The call runs in a bounded thread pool with a timeout (see
        :class:`TelegramSinkConfig`) so the agent's polling thread is
        never blocked for more than ``bridge_timeout_s`` seconds. Its
        return value is appended to the Telegram message before
        dispatch. Exceptions are caught and replaced with an explicit
        marker in the message body.
    config:
        Optional :class:`TelegramSinkConfig` overriding inline
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
        dispatcher: AlarmDispatcher,
        *,
        investigation_bridge: InvestigationBridge | None = None,
        config: TelegramSinkConfig | None = None,
    ) -> None:
        self._dispatcher = dispatcher
        self._investigation_bridge = investigation_bridge
        self._config = config if config is not None else TelegramSinkConfig()
        # The executor is constructed lazily — only created if a bridge
        # is actually configured AND we're not running inline. This
        # keeps the no-investigation hot path zero-cost.
        self._bridge_executor: ThreadPoolExecutor | None = None
        self._bridge_shutdown = False
        self._bridge_lock = threading.Lock()
        if investigation_bridge is not None and not self._config.bridge_run_inline:
            self._bridge_executor = ThreadPoolExecutor(
                max_workers=max(1, self._config.bridge_workers),
                thread_name_prefix="hermes-bridge",
            )

    def __call__(self, incident: HermesIncident) -> None:
        """Format the incident and dispatch it. Never raises."""
        try:
            investigation = self._maybe_investigate(incident)
            message = self._format_message(incident, investigation=investigation)
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
        """Shut down the bridge executor without blocking the caller.

        Safe to call multiple times. This method returns immediately:

        * Queued (not-yet-started) futures are cancelled via
          ``cancel_futures=True`` so they never start after close.
        * Already-running bridge calls are left to complete or time
          out on their own. They are bounded by ``bridge_timeout_s``
          (default 45 s) so they cannot block indefinitely. Using
          ``wait=False`` prevents ``close()`` from hanging when called
          from a SIGTERM handler while a bridge call is in flight.
        """
        # Set shutdown first so in-flight ``_run_bridge_in_pool`` paths that
        # still hold a future can finish, while new work sees ``sink_closed``
        # before racing ``submit`` against ``shutdown``.
        with self._bridge_lock:
            self._bridge_shutdown = True
            executor = self._bridge_executor
            if executor is not None:
                executor.shutdown(wait=False, cancel_futures=True)
                self._bridge_executor = None
        # After close, never fall back to `_run_bridge_inline` just because
        # the executor handle is None — that path would run investigations
        # on the caller thread after shutdown and fight in-flight pool work.

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
        # Pooled mode but executor is gone (e.g. after close): never run inline here.
        return _InvestigationResult.sink_closed()

    def _run_bridge_inline(
        self, bridge: InvestigationBridge, incident: HermesIncident
    ) -> _InvestigationResult:
        try:
            summary = bridge(incident)
        except Exception:
            logger.warning(
                "hermes investigation bridge raised: rule=%s fingerprint=%s",
                incident.rule,
                incident.fingerprint,
                exc_info=True,
            )
            return _InvestigationResult.failed()
        return self._coerce_summary(summary)

    def _run_bridge_in_pool(
        self, bridge: InvestigationBridge, incident: HermesIncident
    ) -> _InvestigationResult:
        # Caller (_maybe_investigate) only reaches this branch when the
        # executor is not None; guard defensively rather than asserting so
        # the path is safe under optimised bytecode (-O) and across any
        # future refactor that may relax the precondition.
        with self._bridge_lock:
            if self._bridge_shutdown:
                return _InvestigationResult.sink_closed()
            executor = self._bridge_executor
            if executor is None:
                return _InvestigationResult.sink_closed()
            try:
                future: Future[str | None] = executor.submit(bridge, incident)
            except RuntimeError:
                # Pool already shut down (TOCTOU with :meth:`close`) — still
                # deliver Telegram; investigation section is skipped only.
                return _InvestigationResult.sink_closed()

        timeout = self._config.bridge_timeout_s
        try:
            summary = future.result(timeout=timeout)
        except FutureTimeoutError:
            # Leave the future running — cancelling a long LLM call
            # mid-flight is rarely clean, and the next matching incident
            # will get a fresh budget after cooldown. Log so operators
            # can correlate timed-out alerts with server-side activity.
            logger.warning(
                "hermes investigation bridge timed out after %.1fs: rule=%s fingerprint=%s",
                timeout,
                incident.rule,
                incident.fingerprint,
            )
            return _InvestigationResult.timed_out(timeout)
        except FutureCancelledError:
            # shutdown(cancel_futures=True) cancels outstanding futures.
            # CancelledError signals sink closure, not an investigation
            # failure, so the operator-visible marker must reflect that.
            return _InvestigationResult.sink_closed()
        except Exception:
            logger.warning(
                "hermes investigation bridge raised: rule=%s fingerprint=%s",
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
    """Internal value type carrying the outcome of a bridge call.

    A :class:`TelegramSink._maybe_investigate` call returns one of several
    states (see module docstring). Encoding them as a small value type
    keeps :meth:`TelegramSink._format_message` branchless and makes the
    operator-visible marker for each state easy to audit in tests.
    """

    state: str  # not_attempted | success | empty | failed | timed_out | sink_closed
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
        # not_attempted → no investigation block, but MEDIUM severity
        # gets an explicit marker so the operator knows the rule is
        # notify-only by design.
        if severity == IncidentSeverity.MEDIUM:
            return "note: warning-burst severity — notify only, no investigation run."
        return ""


def make_telegram_sink(
    dispatcher: AlarmDispatcher,
    *,
    investigation_bridge: InvestigationBridge | None = None,
    config: TelegramSinkConfig | None = None,
) -> IncidentSink:
    """Build an :data:`IncidentSink` callable bound to ``dispatcher``."""
    sink = TelegramSink(
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
    "TelegramSink",
    "TelegramSinkConfig",
    "make_telegram_sink",
]
