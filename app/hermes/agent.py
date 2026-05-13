"""Hermes agent: glue between the file tailer, parser, classifier, and sinks.

Public surface is :class:`HermesAgent`. Construct one with the path to a
Hermes log file (defaults to ``~/.hermes/logs/errors.log``) and a callable
that handles each detected :class:`HermesIncident`. Call :meth:`start` to
spawn the polling thread, :meth:`stop` to shut it down, or use the agent
as a context manager for guaranteed cleanup.

The agent is *I/O bounded*: a single daemon thread polls the log file and
synchronously runs the parser/classifier/sink pipeline. This is fine for
log files written at human-scale rates (Hermes' ``errors.log`` is in the
single-digit lines/second at peak); higher-rate files should consider a
queue between the tailer and the classifier in a follow-up.
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable, Iterable
from pathlib import Path
from types import TracebackType
from typing import Final

from app.hermes.classifier import IncidentClassifier
from app.hermes.incident import HermesIncident, LogLevel
from app.hermes.parser import parse_log_line
from app.hermes.tailer import DEFAULT_POLL_INTERVAL_S, FileTailer

logger = logging.getLogger(__name__)

IncidentSink = Callable[[HermesIncident], None]

DEFAULT_LOG_PATH: Final[Path] = Path.home() / ".hermes" / "logs" / "errors.log"
_THREAD_JOIN_TIMEOUT_S: Final[float] = 2.0


class HermesAgent:
    """Polls a Hermes log file and emits structured incidents.

    Parameters
    ----------
    sink:
        Callable invoked for each detected incident. Exceptions raised by
        the sink are logged but do not stop the polling loop — a buggy
        sink must not silently disable incident detection.
    log_path:
        Path to the Hermes log file. Defaults to
        ``~/.hermes/logs/errors.log``.
    classifier:
        Optional pre-configured :class:`IncidentClassifier`. Construct one
        explicitly when you need non-default thresholds; otherwise the
        agent creates a classifier with the module defaults.
    poll_interval_s, from_start:
        Forwarded to :class:`FileTailer`. ``from_start=True`` replays the
        existing file contents before live tailing, which is useful for
        one-shot scans and tests.
    """

    def __init__(
        self,
        *,
        sink: IncidentSink,
        log_path: Path | str = DEFAULT_LOG_PATH,
        classifier: IncidentClassifier | None = None,
        poll_interval_s: float = DEFAULT_POLL_INTERVAL_S,
        from_start: bool = False,
    ) -> None:
        self._sink = sink
        self._log_path = Path(log_path)
        self._classifier = classifier if classifier is not None else IncidentClassifier()
        self._stop_event = threading.Event()
        self._tailer = FileTailer(
            self._log_path,
            poll_interval_s=poll_interval_s,
            from_start=from_start,
            stop_event=self._stop_event,
        )
        self._thread: threading.Thread | None = None
        self._prev_level: LogLevel | None = None
        # Serializes parser + _prev_level + classifier.observe between the
        # polling thread and synchronous :meth:`process` calls.
        self._pipeline_lock = threading.Lock()

    @property
    def log_path(self) -> Path:
        return self._log_path

    @property
    def is_running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def start(self) -> None:
        """Spawn the polling thread. Idempotent if already running."""
        if self.is_running:
            return
        self._stop_event.clear()
        self._thread = threading.Thread(
            target=self._run,
            name="hermes-agent",
            daemon=True,
        )
        self._thread.start()

    def stop(self, *, timeout: float = _THREAD_JOIN_TIMEOUT_S) -> None:
        """Signal the polling thread and wait until it has exited.

        The poller is joined for up to ``timeout`` seconds first so slow
        shutdown paths can be noticed in logs; if it is still alive after
        that, we block without a further timeout until the thread finishes.
        Only then may :meth:`start` clear the stop event safely — otherwise
        a prior poller could resume after a "stopped" agent appeared idle.
        """
        self._stop_event.set()
        thread = self._thread
        if thread is not None:
            thread.join(timeout=timeout)
            if thread.is_alive():
                logger.warning(
                    "hermes-agent: polling thread still running after %.1fs; "
                    "waiting for clean shutdown",
                    timeout,
                )
                thread.join()
        self._thread = None
        # Surface any tracebacks that were buffered by the classifier so
        # an incident never gets stuck in memory after shutdown.
        with self._pipeline_lock:
            pending = self._classifier.flush()
        for incident in pending:
            self._dispatch(incident)

    def process(self, lines: Iterable[str]) -> list[HermesIncident]:
        """Run the parser/classifier pipeline over an explicit line sequence.

        Useful for one-shot scans (``opensre hermes scan``) and unit tests
        without the polling thread.
        """
        emitted: list[HermesIncident] = []
        for line in lines:
            with self._pipeline_lock:
                record = parse_log_line(line, prev_level=self._prev_level)
                if record is None:
                    continue
                self._prev_level = record.level if not record.is_continuation else self._prev_level
                incidents = list(self._classifier.observe(record))
            for incident in incidents:
                emitted.append(incident)
                self._dispatch(incident)
        with self._pipeline_lock:
            flushed = list(self._classifier.flush())
        for incident in flushed:
            emitted.append(incident)
            self._dispatch(incident)
        return emitted

    def __enter__(self) -> HermesAgent:
        self.start()
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self.stop()

    def _run(self) -> None:
        try:
            for line in self._tailer:
                # Do NOT break here on stop_event: FileTailer.__iter__ already
                # drains _pending_lines before exiting when the stop event fires.
                # An early break here would discard lines that the tailer has
                # already buffered, silently defeating the pending-line drain
                # that was added to prevent exactly that data loss.
                with self._pipeline_lock:
                    record = parse_log_line(line, prev_level=self._prev_level)
                    if record is None:
                        continue
                    if not record.is_continuation:
                        self._prev_level = record.level
                    incidents = list(self._classifier.observe(record))
                for incident in incidents:
                    self._dispatch(incident)
        except Exception:
            # The polling thread is the agent's only worker; if we let an
            # exception propagate we lose all future incidents silently.
            # Log it loudly but keep the process alive — the operator can
            # restart the agent after fixing the underlying cause.
            logger.exception("hermes-agent polling thread crashed")

    def _dispatch(self, incident: HermesIncident) -> None:
        try:
            self._sink(incident)
        except Exception:
            logger.exception(
                "hermes incident sink raised: rule=%s logger=%s",
                incident.rule,
                incident.logger,
            )


__all__ = [
    "DEFAULT_LOG_PATH",
    "HermesAgent",
    "IncidentSink",
]
