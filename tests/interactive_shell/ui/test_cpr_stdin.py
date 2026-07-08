"""Tests for CPR stdin hygiene helpers."""

from __future__ import annotations

import os
import select
import sys
import threading
import time

import pytest

from surfaces.interactive_shell.ui.components.cpr_stdin import (
    contains_cpr_sequence,
    drain_stale_cpr_bytes,
    strip_cpr_sequences,
)


class _FakeTtyStdin:
    """Stdin stand-in whose fileno() is a pipe read end and that reports as a TTY."""

    def __init__(self, fd: int) -> None:
        self._fd = fd

    def isatty(self) -> bool:
        return True

    def fileno(self) -> int:
        return self._fd


def _buffer_has_bytes(fd: int, timeout: float = 0.0) -> bool:
    return bool(select.select([fd], [], [], timeout)[0])


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("\x1b[32;1R", ""),
        ("[32;1R", ""),
        ("\x9b32;1R", ""),
        ("what is our current model?[32;1R", "what is our current model?"),
        ("before \x1b[12;80R after", "before  after"),
        ("7R[25;57R23;57R", ""),
        ("25;57R", ""),
    ],
)
def test_strip_cpr_sequences_removes_terminal_cursor_replies(
    text: str,
    expected: str,
) -> None:
    assert strip_cpr_sequences(text) == expected


def test_contains_cpr_sequence_detects_leaked_bytes() -> None:
    assert contains_cpr_sequence("\x1b[12;80R")
    assert not contains_cpr_sequence("plain prompt text")


def test_drain_consumes_already_buffered_cpr(monkeypatch: pytest.MonkeyPatch) -> None:
    r, w = os.pipe()
    try:
        os.write(w, b"\x1b[4;1R")
        monkeypatch.setattr(sys, "stdin", _FakeTtyStdin(r))
        drain_stale_cpr_bytes()
        assert not _buffer_has_bytes(r)
    finally:
        os.close(r)
        os.close(w)


def test_drain_settle_seconds_waits_for_in_flight_cpr(monkeypatch: pytest.MonkeyPatch) -> None:
    r, w = os.pipe()

    def _write_after_delay() -> None:
        time.sleep(0.05)
        os.write(w, b"\x1b[31;1R")

    writer = threading.Thread(target=_write_after_delay)
    monkeypatch.setattr(sys, "stdin", _FakeTtyStdin(r))
    try:
        writer.start()
        drain_stale_cpr_bytes(settle_seconds=0.5)
        assert not _buffer_has_bytes(r)  # the still-in-flight reply was caught
    finally:
        writer.join()
        os.close(r)
        os.close(w)


def test_drain_without_settle_misses_in_flight_cpr(monkeypatch: pytest.MonkeyPatch) -> None:
    # Contrast case that motivates settle_seconds: the default non-blocking drain
    # returns before a reply that has not arrived yet, leaving it in the buffer.
    r, w = os.pipe()

    def _write_after_delay() -> None:
        time.sleep(0.05)
        os.write(w, b"\x1b[31;1R")

    writer = threading.Thread(target=_write_after_delay)
    monkeypatch.setattr(sys, "stdin", _FakeTtyStdin(r))
    try:
        writer.start()
        drain_stale_cpr_bytes()
        writer.join()
        assert _buffer_has_bytes(r, timeout=0.2)  # not drained without a settle window
    finally:
        os.close(r)
        os.close(w)


def test_drain_is_a_no_op_when_not_a_tty(monkeypatch: pytest.MonkeyPatch) -> None:
    class _NotTty:
        def isatty(self) -> bool:
            return False

        def fileno(self) -> int:
            raise AssertionError("fileno must not be touched when stdin is not a TTY")

    monkeypatch.setattr(sys, "stdin", _NotTty())
    drain_stale_cpr_bytes(settle_seconds=0.5)  # returns immediately, no fd access
