"""Tests for :mod:`app.hermes.tailer`."""

from __future__ import annotations

import threading
import time
from pathlib import Path

import pytest

from app.hermes.tailer import FileTailer


def _drain_for(tailer: FileTailer, *, max_lines: int, timeout_s: float = 2.0) -> list[str]:
    """Pull at most ``max_lines`` from the tailer or stop after ``timeout_s``.

    The tailer's ``__iter__`` blocks between poll cycles; calling ``next()``
    from the test thread would deadlock once the file is fully drained. We
    therefore run consumption on a daemon thread and use ``tailer.stop()``
    plus a thread join as the timeout mechanism.
    """
    out: list[str] = []

    def consume() -> None:
        for line in tailer:
            out.append(line)
            if len(out) >= max_lines:
                break

    thread = threading.Thread(target=consume, daemon=True)
    thread.start()
    thread.join(timeout=timeout_s)
    tailer.stop()
    thread.join(timeout=1.0)
    return out


class TestFromStartReplay:
    def test_mid_chunk_close_resumes_without_skipping_lines(self, tmp_path: Path) -> None:
        """Closing the iterator mid-chunk must not advance the file cursor past
        unconsumed complete lines (regression for chunk read / yield ordering).
        """
        log = tmp_path / "errors.log"
        body = "\n".join(f"L{i:03d}" for i in range(40)) + "\n"
        log.write_text(body, encoding="utf-8")

        tailer = FileTailer(log, poll_interval_s=0.01, from_start=True, read_chunk=80)
        outer = iter(tailer)
        assert next(outer) == "L000"
        assert next(outer) == "L001"
        outer.close()

        resumed: list[str] = []
        for line in tailer:
            resumed.append(line)
            if len(resumed) == 38:
                break
        tailer.stop()

        assert resumed[:3] == ["L002", "L003", "L004"]
        assert resumed[-1] == "L039"
        assert len(resumed) == 38

    def test_yields_existing_lines_in_order(self, tmp_path: Path) -> None:
        log = tmp_path / "errors.log"
        log.write_text("a\nb\nc\n", encoding="utf-8")

        tailer = FileTailer(log, poll_interval_s=0.01, from_start=True)
        out = _drain_for(tailer, max_lines=3)

        assert out == ["a", "b", "c"]

    def test_partial_trailing_line_held_until_complete(self, tmp_path: Path) -> None:
        log = tmp_path / "errors.log"
        log.write_text("complete\npartial", encoding="utf-8")

        tailer = FileTailer(log, poll_interval_s=0.01, from_start=True)
        out = _drain_for(tailer, max_lines=2, timeout_s=0.3)

        assert out == ["complete"]

    def test_stop_does_not_drop_pending_lines_when_file_is_idle(self, tmp_path: Path) -> None:
        """Pending complete lines must still drain after stop even when file size
        no longer grows (regression for stat-size early return path).
        """
        log = tmp_path / "errors.log"
        log.write_text("a\nb\nc\n", encoding="utf-8")
        tailer = FileTailer(log, poll_interval_s=0.01, from_start=True, read_chunk=4096)

        it = iter(tailer)
        assert next(it) == "a"
        tailer.stop()

        drained = list(it)
        assert drained == ["b", "c"]


class TestLiveTail:
    def test_only_yields_lines_appended_after_attach(self, tmp_path: Path) -> None:
        log = tmp_path / "errors.log"
        log.write_text("before\n", encoding="utf-8")

        stop_event = threading.Event()
        tailer = FileTailer(log, poll_interval_s=0.01, stop_event=stop_event)

        results: list[str] = []

        def consumer() -> None:
            for line in tailer:
                results.append(line)
                if len(results) >= 1:
                    stop_event.set()
                    break

        thread = threading.Thread(target=consumer)
        thread.start()
        time.sleep(0.05)
        with log.open("a", encoding="utf-8") as fh:
            fh.write("after\n")
        thread.join(timeout=2)

        assert results == ["after"]


class TestRotationAndTruncation:
    def test_truncate_reopens_from_zero(self, tmp_path: Path) -> None:
        log = tmp_path / "errors.log"
        log.write_text("first\nsecond\n", encoding="utf-8")

        tailer = FileTailer(log, poll_interval_s=0.01, from_start=True)
        iterator = iter(tailer)

        first = [next(iterator) for _ in range(2)]
        assert first == ["first", "second"]

        log.write_text("rotated\n", encoding="utf-8")
        rotated = next(iterator)
        tailer.stop()

        assert rotated == "rotated"

    def test_missing_file_does_not_raise(self, tmp_path: Path) -> None:
        log = tmp_path / "missing.log"
        tailer = FileTailer(log, poll_interval_s=0.01)
        out = _drain_for(tailer, max_lines=1, timeout_s=0.2)

        assert out == []


class TestValidation:
    @pytest.mark.parametrize("interval", [0, -1.5])
    def test_poll_interval_must_be_positive(self, tmp_path: Path, interval: float) -> None:
        with pytest.raises(ValueError, match="poll_interval_s"):
            FileTailer(tmp_path / "log", poll_interval_s=interval)

    def test_read_chunk_must_be_positive(self, tmp_path: Path) -> None:
        with pytest.raises(ValueError, match="read_chunk"):
            FileTailer(tmp_path / "log", read_chunk=0)
