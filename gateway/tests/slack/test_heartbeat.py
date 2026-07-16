"""Tests for the Slack gateway liveness heartbeat."""

from __future__ import annotations

import threading
from pathlib import Path

from gateway.slack.heartbeat import ConnectionHeartbeat


def test_touch_creates_file_and_missing_parents(tmp_path: Path) -> None:
    # Arrange: a heartbeat path whose parent directory does not exist yet.
    path = tmp_path / "nested" / "gateway.heartbeat"
    heartbeat = ConnectionHeartbeat(path=str(path), is_alive=lambda: True)

    # Act
    heartbeat.touch()

    # Assert
    assert path.exists()


def test_start_writes_an_initial_heartbeat(tmp_path: Path) -> None:
    # Arrange
    path = tmp_path / "gateway.heartbeat"
    heartbeat = ConnectionHeartbeat(path=str(path), is_alive=lambda: False, interval_seconds=60)

    # Act: start writes immediately, before the first tick interval elapses.
    heartbeat.start()
    heartbeat.stop()

    # Assert
    assert path.exists()


def test_ticker_refreshes_the_heartbeat_while_connection_is_alive(tmp_path: Path) -> None:
    # Arrange: a live connection; signal once the ticker has run at least once.
    path = tmp_path / "gateway.heartbeat"
    ticked = threading.Event()

    def is_alive() -> bool:
        ticked.set()
        return True

    heartbeat = ConnectionHeartbeat(path=str(path), is_alive=is_alive, interval_seconds=0.01)
    heartbeat.start()
    initial_mtime_ns = path.stat().st_mtime_ns

    # Act: wait for a tick, then stop (join lets the in-flight touch complete).
    assert ticked.wait(timeout=2.0)
    heartbeat.stop()

    # Assert: the file's mtime advanced past the initial start() write.
    assert path.stat().st_mtime_ns > initial_mtime_ns


def test_ticker_stops_refreshing_when_connection_is_not_alive(tmp_path: Path) -> None:
    # Arrange: a dead connection; signal once the ticker has checked is_alive.
    path = tmp_path / "gateway.heartbeat"
    checked = threading.Event()

    def is_alive() -> bool:
        checked.set()
        return False

    heartbeat = ConnectionHeartbeat(path=str(path), is_alive=is_alive, interval_seconds=0.01)
    heartbeat.start()
    initial_mtime_ns = path.stat().st_mtime_ns

    # Act: wait until the ticker has run and observed the dead connection.
    assert checked.wait(timeout=2.0)
    heartbeat.stop()

    # Assert: no refresh happened — mtime is still the initial start() write.
    assert path.stat().st_mtime_ns == initial_mtime_ns
