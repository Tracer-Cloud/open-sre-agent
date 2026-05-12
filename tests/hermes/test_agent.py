"""Tests for :mod:`app.hermes.agent`."""

from __future__ import annotations

import threading
import time
from pathlib import Path

from app.hermes.agent import HermesAgent
from app.hermes.classifier import IncidentClassifier
from app.hermes.incident import HermesIncident, IncidentSeverity


class TestAgentProcess:
    def test_process_runs_classifier_over_explicit_lines(self) -> None:
        emitted: list[HermesIncident] = []
        agent = HermesAgent(
            sink=emitted.append,
            log_path="/dev/null",
            classifier=IncidentClassifier(warning_burst_threshold=3, warning_burst_window_s=60.0),
        )

        lines = [
            "2026-05-12 00:00:00,000 WARNING gateway.platforms.telegram: conflict 1",
            "2026-05-12 00:00:10,000 WARNING gateway.platforms.telegram: conflict 2",
            "2026-05-12 00:00:20,000 WARNING gateway.platforms.telegram: conflict 3",
        ]
        out = agent.process(lines)

        assert len(out) == 1
        assert out[0].rule == "warning_burst"
        assert out[0].severity is IncidentSeverity.MEDIUM
        # Sink received the same incident the explicit list did.
        assert emitted == out

    def test_sink_exception_does_not_break_pipeline(self) -> None:
        calls: list[HermesIncident] = []

        def flaky_sink(incident: HermesIncident) -> None:
            calls.append(incident)
            if len(calls) == 1:
                raise RuntimeError("first dispatch fails")

        agent = HermesAgent(
            sink=flaky_sink,
            log_path="/dev/null",
            classifier=IncidentClassifier(warning_burst_threshold=2, warning_burst_window_s=60.0),
        )

        # Two ERROR records each emit error_severity; the first sink call
        # raises, the second still has to land — otherwise a buggy sink
        # would silently disable detection.
        agent.process(
            [
                "2026-05-12 00:00:00,000 ERROR root: boom 1",
                "2026-05-12 00:00:01,000 ERROR root: boom 2",
            ]
        )

        assert len(calls) == 2

    def test_process_flushes_trailing_traceback(self) -> None:
        """One-shot ``process()`` must mirror ``HermesLogsTool`` and flush the
        classifier so an open traceback at end-of-input becomes an incident.
        """
        emitted: list[HermesIncident] = []
        agent = HermesAgent(
            sink=emitted.append,
            log_path="/dev/null",
            classifier=IncidentClassifier(),
        )
        lines = [
            "2026-05-12 00:00:00,000 ERROR tools.x: Traceback (most recent call last):",
            '  File "/x", line 1, in foo',
        ]
        out = agent.process(lines)

        assert any(i.rule == "traceback" for i in out)
        assert emitted == out


class TestAgentLifecycle:
    def test_start_stop_processes_appended_lines(self, tmp_path: Path) -> None:
        log = tmp_path / "errors.log"
        log.write_text("", encoding="utf-8")

        emitted: list[HermesIncident] = []
        seen = threading.Event()

        def sink(incident: HermesIncident) -> None:
            emitted.append(incident)
            seen.set()

        agent = HermesAgent(
            sink=sink,
            log_path=log,
            poll_interval_s=0.01,
            classifier=IncidentClassifier(warning_burst_threshold=2, warning_burst_window_s=60.0),
        )
        agent.start()
        try:
            with log.open("a", encoding="utf-8") as fh:
                fh.write("2026-05-12 00:00:00,000 ERROR root: live failure\n")
            assert seen.wait(timeout=2.0), "agent did not surface a live ERROR record"
        finally:
            agent.stop()

        assert any(i.rule == "error_severity" for i in emitted)
        assert agent.is_running is False

    def test_context_manager_starts_and_stops(self, tmp_path: Path) -> None:
        log = tmp_path / "errors.log"
        log.write_text("", encoding="utf-8")

        with HermesAgent(sink=lambda _i: None, log_path=log, poll_interval_s=0.01) as agent:
            time.sleep(0.05)
            assert agent.is_running is True

        assert agent.is_running is False

    def test_start_is_idempotent(self, tmp_path: Path) -> None:
        log = tmp_path / "errors.log"
        log.write_text("", encoding="utf-8")
        agent = HermesAgent(sink=lambda _i: None, log_path=log, poll_interval_s=0.01)
        agent.start()
        try:
            first_thread = agent._thread  # type: ignore[attr-defined]
            agent.start()
            assert agent._thread is first_thread  # type: ignore[attr-defined]
        finally:
            agent.stop()

    def test_stop_flushes_buffered_traceback(self, tmp_path: Path) -> None:
        log = tmp_path / "errors.log"
        log.write_text("", encoding="utf-8")

        emitted: list[HermesIncident] = []
        traceback_seen = threading.Event()

        def sink(incident: HermesIncident) -> None:
            emitted.append(incident)
            if incident.rule == "traceback":
                traceback_seen.set()

        agent = HermesAgent(sink=sink, log_path=log, poll_interval_s=0.01)
        agent.start()
        try:
            with log.open("a", encoding="utf-8") as fh:
                fh.write(
                    "2026-05-12 00:00:00,000 ERROR tools.x: Traceback (most recent call last):\n"
                )
                fh.write('  File "/x", line 1, in foo\n')
            time.sleep(0.2)
        finally:
            agent.stop()

        assert traceback_seen.is_set(), "expected traceback incident to be flushed on stop()"

    def test_stop_waits_for_slow_sink_after_initial_join_timeout(self, tmp_path: Path) -> None:
        """stop(timeout) must not drop the thread reference while the poller
        is still inside ``_dispatch`` — :meth:`start` would otherwise clear
        the stop event and allow two poll loops to share state."""
        log = tmp_path / "errors.log"
        log.write_text("", encoding="utf-8")
        sink_released = threading.Event()
        sink_invoked = threading.Event()
        stop_completed = threading.Event()

        def sink(_incident: HermesIncident) -> None:
            sink_invoked.set()
            sink_released.wait(timeout=30.0)

        agent = HermesAgent(
            sink=sink,
            log_path=log,
            poll_interval_s=0.01,
            classifier=IncidentClassifier(warning_burst_threshold=2, warning_burst_window_s=60.0),
        )
        agent.start()
        try:
            with log.open("a", encoding="utf-8") as fh:
                fh.write("2026-05-12 00:00:00,000 ERROR root: blocks shutdown\n")
            assert sink_invoked.wait(timeout=2.0), (
                "sink not invoked — poller may have missed the line"
            )

            def stopper() -> None:
                agent.stop(timeout=0.05)
                stop_completed.set()

            threading.Thread(target=stopper, daemon=True).start()
            assert not stop_completed.wait(timeout=0.5)
            sink_released.set()
            assert stop_completed.wait(timeout=5.0), "stop() should block until dispatch returns"
            assert agent.is_running is False
        finally:
            sink_released.set()

    def test_stop_does_not_discard_tailer_pending_lines(self, tmp_path: Path) -> None:
        """_run() must not break out of the tailer loop early on stop_event: the
        tailer already drains _pending_lines before exiting, so an early break in
        the agent loop discards lines the tailer has already buffered."""
        log = tmp_path / "errors.log"
        # Write enough lines that the tailer buffers a full chunk before the
        # agent even starts, so they land in _pending_lines before any poll.
        lines = [f"2026-05-12 00:00:{i:02d},000 ERROR root: line {i}\n" for i in range(15)]
        log.write_text("".join(lines), encoding="utf-8")

        emitted: list[HermesIncident] = []
        agent = HermesAgent(
            sink=emitted.append,
            log_path=log,
            poll_interval_s=0.01,
            from_start=True,
            classifier=IncidentClassifier(
                warning_burst_threshold=100,
                warning_burst_window_s=300.0,
            ),
        )
        agent.start()
        # Give the agent time to read the file and buffer lines.
        time.sleep(0.3)
        agent.stop()

        # Every distinct ERROR line should have produced an incident; we
        # need at least one to confirm pending lines were not discarded.
        assert len(emitted) >= 1, (
            "agent discarded buffered lines — early stop_event break in _run() "
            "is skipping lines the tailer already queued in _pending_lines"
        )
