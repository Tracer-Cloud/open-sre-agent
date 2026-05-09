"""Unit tests for ``app.agents.tail``.

Covers the four invariants the parent issue (#1493) and the design
review hammered on:

* ``attach()`` validates *eagerly* — :class:`AttachUnsupported` lands
  at the call site, never as a deferred ``StopIteration`` surprise.
* :class:`TailBuffer` caps memory at ``DEFAULT_MAX_BYTES`` and drops
  *whole chunks* so UTF-8 decoding never splits a codepoint.
* The internal queue is bounded; producer-side overflow drops the
  *oldest* chunk to keep the live tail fresh under burst writers.
* Only regular files are accepted on either platform; TTY/PTY/pipe/
  socket/anon_inode/``/dev/null`` are rejected with a precise reason.
"""

from __future__ import annotations

import os
import queue
import threading
import time
from pathlib import Path
from unittest.mock import patch

import pytest

from app.agents import tail as tail_mod
from app.agents.tail import (
    DEFAULT_MAX_BYTES,
    AttachSession,
    AttachUnsupported,
    TailBuffer,
    _parse_lsof_fd1,
    _resolve_linux_target,
    _resolve_macos_target,
    _resolve_target,
    _ResolvedTarget,
    attach,
)


def _drain_until(sess: AttachSession, predicate, *, timeout: float) -> bytes:
    """Drain chunks until ``predicate(accumulated_bytes)`` is True or
    the timeout elapses. Returns the accumulated bytes (or whatever was
    seen before the deadline)."""
    deadline = time.monotonic() + timeout
    accumulated = b""
    iterator = iter(sess)
    while time.monotonic() < deadline:
        try:
            chunk = next(iterator)
        except StopIteration:
            break
        accumulated += chunk
        if predicate(accumulated):
            return accumulated
    return accumulated


class TestTailBuffer:
    def test_empty_after_construction(self) -> None:
        buf = TailBuffer()
        assert len(buf) == 0
        assert buf.snapshot() == b""
        assert buf.decoded() == ""

    def test_append_empty_chunk_is_noop(self) -> None:
        buf = TailBuffer()
        buf.append(b"")
        assert len(buf) == 0

    def test_append_small_chunk(self) -> None:
        buf = TailBuffer()
        buf.append(b"hello")
        assert len(buf) == 5
        assert buf.snapshot() == b"hello"
        assert buf.decoded() == "hello"

    def test_drops_whole_chunks_past_cap(self) -> None:
        buf = TailBuffer(max_bytes=10)
        # Each chunk fits but together they exceed the cap.
        buf.append(b"AAAA")  # 4
        buf.append(b"BBBB")  # 8
        buf.append(b"CCCC")  # 12 -> drops "AAAA"
        assert len(buf) == 8
        assert buf.snapshot() == b"BBBBCCCC"
        buf.append(b"DDDD")  # 12 -> drops "BBBB"
        assert len(buf) == 8
        assert buf.snapshot() == b"CCCCDDDD"

    def test_retains_single_oversized_chunk(self) -> None:
        # A single append larger than the cap must still be visible —
        # silently dropping it would lose information the user can't
        # see anywhere else.
        buf = TailBuffer(max_bytes=10)
        big = b"X" * 100
        buf.append(big)
        assert len(buf) == 100
        assert buf.snapshot() == big

    def test_decoded_does_not_split_emoji_across_chunks(self) -> None:
        # 🦀 = b"\xf0\x9f\xa6\x80" (4 bytes). Splitting at byte 2 would
        # yield two invalid sequences if decode happened mid-chunk; we
        # hold whole chunks so the decode boundary is between chunks.
        buf = TailBuffer(max_bytes=DEFAULT_MAX_BYTES)
        buf.append(b"\xf0\x9f")
        buf.append(b"\xa6\x80 done")
        assert buf.decoded() == "🦀 done"

    def test_decoded_replaces_invalid_utf8(self) -> None:
        buf = TailBuffer(max_bytes=DEFAULT_MAX_BYTES)
        buf.append(b"\xff\xfe")
        # errors="replace" yields U+FFFD, never raises.
        decoded = buf.decoded()
        assert "�" in decoded

    def test_rejects_zero_or_negative_cap(self) -> None:
        with pytest.raises(ValueError):
            TailBuffer(max_bytes=0)
        with pytest.raises(ValueError):
            TailBuffer(max_bytes=-1)

    def test_cap_bounds_under_burst(self) -> None:
        buf = TailBuffer(max_bytes=4 * 1024)  # 4 KiB cap, easy to exercise
        chunk = b"X" * 256
        for _ in range(64):  # 16 KiB total
            buf.append(chunk)
        # Soft cap: peak <= cap + last chunk
        assert len(buf) <= 4 * 1024 + len(chunk)
        # And everything we kept is suffix-of-the-stream (last chunks first)
        assert buf.snapshot().endswith(chunk * 4)


class TestParseLsofFd1:
    def test_finds_fd1_in_middle_of_output(self) -> None:
        stdout = "p1234\nf0\ntCHR\nn/dev/ttys000\nf1\ntREG\nn/tmp/log\nf2\ntREG\nn/tmp/err\n"
        assert _parse_lsof_fd1(stdout) == ("REG", "/tmp/log")

    def test_finds_fd1_at_eof(self) -> None:
        stdout = "p1234\nf0\ntCHR\nn/dev/ttys000\nf1\ntREG\nn/tmp/log\n"
        assert _parse_lsof_fd1(stdout) == ("REG", "/tmp/log")

    def test_returns_chr_for_terminal(self) -> None:
        stdout = "p1234\nf1\ntCHR\nn/dev/ttys001\n"
        assert _parse_lsof_fd1(stdout) == ("CHR", "/dev/ttys001")

    def test_returns_pipe(self) -> None:
        stdout = "p1234\nf1\ntPIPE\nnpipe\n"
        assert _parse_lsof_fd1(stdout) == ("PIPE", "pipe")

    def test_missing_fd1_returns_none(self) -> None:
        stdout = "p1234\nf0\ntCHR\nn/dev/ttys000\nf2\ntREG\nn/tmp/err\n"
        assert _parse_lsof_fd1(stdout) == (None, None)

    def test_ignores_unknown_fields(self) -> None:
        stdout = "p1234\nf1\nax\nl \ntREG\nn/tmp/log\n"
        assert _parse_lsof_fd1(stdout) == ("REG", "/tmp/log")

    def test_handles_empty_stdout(self) -> None:
        assert _parse_lsof_fd1("") == (None, None)


class TestResolveLinuxTarget:
    def test_regular_file_target(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        log = tmp_path / "out.log"
        log.write_text("")
        monkeypatch.setattr(os, "readlink", lambda _path: str(log))
        target = _resolve_linux_target(123)
        assert target.pid == 123
        assert target.path == log

    def test_socket_rejected(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(os, "readlink", lambda _path: "socket:[1234]")
        with pytest.raises(AttachUnsupported, match="socket"):
            _resolve_linux_target(123)

    def test_pipe_rejected(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(os, "readlink", lambda _path: "pipe:[5678]")
        with pytest.raises(AttachUnsupported, match="pipe"):
            _resolve_linux_target(123)

    def test_anon_inode_rejected(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(os, "readlink", lambda _path: "anon_inode:[eventfd]")
        with pytest.raises(AttachUnsupported, match="anon_inode"):
            _resolve_linux_target(123)

    def test_pts_rejected(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(os, "readlink", lambda _path: "/dev/pts/3")
        with pytest.raises(AttachUnsupported, match="terminal"):
            _resolve_linux_target(123)

    def test_dev_null_rejected(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(os, "readlink", lambda _path: "/dev/null")
        with pytest.raises(AttachUnsupported, match="/dev/null"):
            _resolve_linux_target(123)

    def test_non_path_target_rejected(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(os, "readlink", lambda _path: "[event_poll]")
        with pytest.raises(AttachUnsupported, match="not a filesystem path"):
            _resolve_linux_target(123)

    def test_permission_denied_on_readlink(self, monkeypatch: pytest.MonkeyPatch) -> None:
        def _denied(path: str | os.PathLike[str]) -> str:
            raise PermissionError(13, "perm denied")

        monkeypatch.setattr(os, "readlink", _denied)
        with pytest.raises(AttachUnsupported, match="permission"):
            _resolve_linux_target(123)

    def test_no_such_pid_on_readlink(self, monkeypatch: pytest.MonkeyPatch) -> None:
        def _missing(path: str | os.PathLike[str]) -> str:
            raise FileNotFoundError(2, "no such")

        monkeypatch.setattr(os, "readlink", _missing)
        with pytest.raises(AttachUnsupported, match="no such pid"):
            _resolve_linux_target(123)

    def test_target_is_directory_rejected(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Even if readlink returns a real path, it must be a regular file.
        monkeypatch.setattr(os, "readlink", lambda _path: str(tmp_path))
        with pytest.raises(AttachUnsupported, match="not a regular file"):
            _resolve_linux_target(123)


class _Completed:
    """Minimal subprocess.CompletedProcess stand-in for tests."""

    def __init__(self, *, returncode: int = 0, stdout: str = "", stderr: str = "") -> None:
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


class TestResolveMacosTarget:
    def test_regular_file(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        log = tmp_path / "out.log"
        log.write_text("")
        stdout = f"p1234\nf0\ntCHR\nn/dev/ttys000\nf1\ntREG\nn{log}\n"
        monkeypatch.setattr(
            tail_mod.subprocess, "run", lambda *_a, **_kw: _Completed(stdout=stdout)
        )
        target = _resolve_macos_target(1234)
        assert target.pid == 1234
        assert target.path == log

    def test_chr_rejected(self, monkeypatch: pytest.MonkeyPatch) -> None:
        stdout = "p1234\nf1\ntCHR\nn/dev/ttys001\n"
        monkeypatch.setattr(
            tail_mod.subprocess, "run", lambda *_a, **_kw: _Completed(stdout=stdout)
        )
        with pytest.raises(AttachUnsupported, match="terminal"):
            _resolve_macos_target(1234)

    def test_pipe_rejected(self, monkeypatch: pytest.MonkeyPatch) -> None:
        stdout = "p1234\nf1\ntPIPE\nnpipe\n"
        monkeypatch.setattr(
            tail_mod.subprocess, "run", lambda *_a, **_kw: _Completed(stdout=stdout)
        )
        with pytest.raises(AttachUnsupported, match="pipe"):
            _resolve_macos_target(1234)

    def test_socket_rejected(self, monkeypatch: pytest.MonkeyPatch) -> None:
        stdout = "p1234\nf1\ntIPv4\nn*:0\n"
        monkeypatch.setattr(
            tail_mod.subprocess, "run", lambda *_a, **_kw: _Completed(stdout=stdout)
        )
        with pytest.raises(AttachUnsupported, match="socket"):
            _resolve_macos_target(1234)

    def test_no_fd1_rejected(self, monkeypatch: pytest.MonkeyPatch) -> None:
        stdout = "p1234\nf0\ntCHR\nn/dev/ttys000\nf2\ntREG\nn/tmp/err\n"
        monkeypatch.setattr(
            tail_mod.subprocess, "run", lambda *_a, **_kw: _Completed(stdout=stdout)
        )
        with pytest.raises(AttachUnsupported, match="no fd 1"):
            _resolve_macos_target(1234)

    def test_no_such_pid(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(
            tail_mod.subprocess,
            "run",
            lambda *_a, **_kw: _Completed(returncode=1, stdout="", stderr="No such pid"),
        )
        with pytest.raises(AttachUnsupported, match="no such pid"):
            _resolve_macos_target(1234)

    def test_lsof_warnings_on_stderr_do_not_break_parse(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        log = tmp_path / "out.log"
        log.write_text("")
        stdout = f"p1234\nf1\ntREG\nn{log}\n"
        # Some lsof builds emit "WARNING: …" on stderr even on success;
        # we must only consult stdout.
        monkeypatch.setattr(
            tail_mod.subprocess,
            "run",
            lambda *_a, **_kw: _Completed(
                returncode=0,
                stdout=stdout,
                stderr="lsof: WARNING: can't stat() …\n",
            ),
        )
        target = _resolve_macos_target(1234)
        assert target.path == log

    def test_lsof_missing(self, monkeypatch: pytest.MonkeyPatch) -> None:
        def _missing(*a: object, **kw: object) -> _Completed:
            raise FileNotFoundError(2, "lsof")

        monkeypatch.setattr(tail_mod.subprocess, "run", _missing)
        with pytest.raises(AttachUnsupported, match="lsof not found"):
            _resolve_macos_target(1234)

    def test_dev_null_rejected_with_specific_message(self, monkeypatch: pytest.MonkeyPatch) -> None:
        # ``/dev/null`` shows up as ``CHR``; the generic "terminal" reject
        # would be misleading. Confirm the dedicated branch fires.
        stdout = "p1234\nf1\ntCHR\nn/dev/null\n"
        monkeypatch.setattr(
            tail_mod.subprocess, "run", lambda *_a, **_kw: _Completed(stdout=stdout)
        )
        with pytest.raises(AttachUnsupported, match="/dev/null"):
            _resolve_macos_target(1234)

    def test_fd1_with_no_type_field_gives_specific_message(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # If lsof returns an fd 1 block with a name but no type field,
        # the error must distinguish "no type" from "no fd 1 entry".
        stdout = "p1234\nf1\nn/some/path\n"
        monkeypatch.setattr(
            tail_mod.subprocess, "run", lambda *_a, **_kw: _Completed(stdout=stdout)
        )
        with pytest.raises(AttachUnsupported, match="no type"):
            _resolve_macos_target(1234)


class TestResolveTargetDispatch:
    def test_windows_rejected(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(tail_mod.sys, "platform", "win32")
        with pytest.raises(AttachUnsupported, match="Windows"):
            _resolve_target(1234)

    def test_no_such_pid_rejected(self, monkeypatch: pytest.MonkeyPatch) -> None:
        # platform is not "win32" so we hit the pid_exists check
        monkeypatch.setattr(tail_mod.sys, "platform", "linux")
        monkeypatch.setattr(tail_mod, "pid_exists", lambda _pid: False)
        with pytest.raises(AttachUnsupported, match="no such pid"):
            _resolve_target(99_999_999)

    def test_unknown_platform_rejected(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(tail_mod.sys, "platform", "freebsd13")
        monkeypatch.setattr(tail_mod, "pid_exists", lambda _pid: True)
        with pytest.raises(AttachUnsupported, match="freebsd13"):
            _resolve_target(1234)

    @pytest.mark.parametrize("invalid_pid", [0, -1, -99])
    def test_non_positive_pid_rejected_before_pid_exists(
        self, monkeypatch: pytest.MonkeyPatch, invalid_pid: int
    ) -> None:
        # Regression guard: ``psutil.pid_exists(0)`` can raise
        # ``PermissionError`` on macOS. The slash handler only catches
        # ``AttachUnsupported``, so an unguarded probe would crash the
        # REPL. ``_resolve_target`` must reject non-positive ids before
        # touching ``pid_exists`` at all.
        def _boom(_pid: int) -> bool:
            raise PermissionError(1, "operation not permitted")

        monkeypatch.setattr(tail_mod.sys, "platform", "darwin")
        monkeypatch.setattr(tail_mod, "pid_exists", _boom)
        with pytest.raises(AttachUnsupported, match="must be positive"):
            _resolve_target(invalid_pid)


class TestAttachEagerValidation:
    def test_attach_raises_immediately_on_unsupported(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # The contract: AttachUnsupported lands on the call to attach(),
        # not deferred to first iteration. Regression guard for the
        # design-review point #1.
        def _fail(_pid: int) -> _ResolvedTarget:
            raise AttachUnsupported("planned failure")

        monkeypatch.setattr(tail_mod, "_resolve_target", _fail)
        with pytest.raises(AttachUnsupported, match="planned failure"):
            attach(1234)


class TestAttachSession:
    def _make_session(self, path: Path, **kwargs: object) -> AttachSession:
        target = _ResolvedTarget(pid=os.getpid(), path=path)
        return AttachSession(target, poll_interval_s=0.01, **kwargs)  # type: ignore[arg-type]

    def test_yields_chunks_written_after_attach(self, tmp_path: Path) -> None:
        log = tmp_path / "agent.log"
        log.write_text("")
        with self._make_session(log) as sess:
            # Write *after* attach so we exercise the post-EOF poll path.
            with log.open("ab") as fh:
                fh.write(b"hello\n")
            seen = _drain_until(sess, lambda b: b"hello" in b, timeout=3.0)
            assert b"hello" in seen
            # Append-on-yield contract: ``buffer`` mirrors what the
            # consumer has seen — the slash-command renderer relies on
            # this so the caller never has to feed ``buffer`` manually.
            assert b"hello" in sess.buffer.snapshot()
        assert not sess._thread.is_alive()  # noqa: SLF001 (test inspecting lifecycle)

    def test_reader_follows_inode_through_rename(self, tmp_path: Path) -> None:
        # logrotate-style: the path is renamed away after attach, but the
        # producer keeps writing to the original inode. The reader holds
        # a fd to that inode and must keep tailing — pathname existence
        # is intentionally not a liveness condition.
        log = tmp_path / "agent.log"
        log.write_text("")
        with self._make_session(log) as sess:
            with log.open("ab") as writer:
                # Rename the path mid-flight; the writer fd still points
                # to the same inode, so the next write lands where the
                # reader is tailing.
                log.rename(tmp_path / "agent.log.1")
                writer.write(b"after-rotate\n")
                writer.flush()
            seen = _drain_until(sess, lambda b: b"after-rotate" in b, timeout=3.0)
            assert b"after-rotate" in seen
        assert not sess._thread.is_alive()  # noqa: SLF001

    def test_iteration_after_close_raises_stop_iteration(self, tmp_path: Path) -> None:
        log = tmp_path / "agent.log"
        log.write_text("")
        sess = self._make_session(log)
        sess.close()
        # The reader's finally posts the sentinel before exiting, so a
        # subsequent ``next()`` lands cleanly on StopIteration without
        # blocking on the queue.
        with pytest.raises(StopIteration):
            next(iter(sess))

    def test_does_not_replay_pre_attach_content(self, tmp_path: Path) -> None:
        # Documenting the limitation — bytes written before attach()
        # are NOT visible. The reader seeks to EOF on construction.
        log = tmp_path / "agent.log"
        log.write_bytes(b"already-here")
        with self._make_session(log) as sess:
            # Give the reader a couple of poll cycles to (not) push.
            time.sleep(0.1)
            items: list[object] = []
            while True:
                try:
                    items.append(sess._queue.get_nowait())  # noqa: SLF001
                except queue.Empty:
                    break
            byte_items = [i for i in items if isinstance(i, bytes)]
            assert byte_items == []

    def test_close_is_idempotent(self, tmp_path: Path) -> None:
        log = tmp_path / "agent.log"
        log.write_text("")
        sess = self._make_session(log)
        sess.close()
        sess.close()  # second call must not raise
        assert not sess._thread.is_alive()  # noqa: SLF001

    def test_context_manager_closes_thread(self, tmp_path: Path) -> None:
        log = tmp_path / "agent.log"
        log.write_text("")
        with self._make_session(log) as sess:
            thread = sess._thread  # noqa: SLF001
            assert thread.is_alive()
        assert not thread.is_alive()

    def test_publish_drops_oldest_when_queue_full(self, tmp_path: Path) -> None:
        # Drop-oldest semantics under burst writes: the queue keeps the
        # *latest* chunks (live tail freshness) and the TailBuffer cap
        # preserves the acceptance memory bound regardless.
        log = tmp_path / "agent.log"
        log.write_text("")
        # Long poll keeps the reader idle while we exercise _publish.
        target = _ResolvedTarget(pid=os.getpid(), path=log)
        with AttachSession(target, queue_max=2, poll_interval_s=10.0) as sess:
            sess._stop_event.set()  # noqa: SLF001
            sess._thread.join(timeout=2.0)  # noqa: SLF001
            assert not sess._thread.is_alive()  # noqa: SLF001
            # Drain the sentinel the reader posted on exit.
            while True:
                try:
                    sess._queue.get_nowait()  # noqa: SLF001
                except queue.Empty:
                    break
            sess._publish(b"a")  # noqa: SLF001
            sess._publish(b"b")  # noqa: SLF001
            sess._publish(b"c")  # noqa: SLF001  -> drops "a"
            drained: list[object] = []
            while True:
                try:
                    drained.append(sess._queue.get_nowait())  # noqa: SLF001
                except queue.Empty:
                    break
            assert drained == [b"b", b"c"]

    def test_reader_exits_when_pid_dies(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        log = tmp_path / "agent.log"
        log.write_text("")
        # Pretend the process is alive once (so the reader enters the
        # loop), then dead — the next poll cycle should exit.
        calls = {"n": 0}

        def _fake_pid_exists(pid: int) -> bool:
            calls["n"] += 1
            return calls["n"] <= 1

        monkeypatch.setattr(tail_mod, "pid_exists", _fake_pid_exists)
        with self._make_session(log) as sess:
            # Wait up to 2s for the reader thread to exit naturally.
            deadline = time.monotonic() + 2.0
            while time.monotonic() < deadline and sess._thread.is_alive():  # noqa: SLF001
                time.sleep(0.01)
            assert not sess._thread.is_alive()  # noqa: SLF001
            # ``producer_exited`` is the signal the slash-command trailer
            # uses to print "· process exited" — must flip True only on
            # the pid-death path, not on close() / OSError.
            assert sess.producer_exited is True
            # Sentinel should be the next item.
            with pytest.raises(StopIteration):
                next(iter(sess))

    def test_close_does_not_set_producer_exited(self, tmp_path: Path) -> None:
        # User-initiated close must NOT look like the producer died —
        # otherwise every Ctrl+C would print a misleading "process
        # exited" line in the slash-command trailer.
        log = tmp_path / "agent.log"
        log.write_text("")
        with self._make_session(log) as sess:
            pass
        assert sess.producer_exited is False


class TestAttachIntegration:
    def test_attach_eagerly_opens_real_file(self, tmp_path: Path) -> None:
        # Skip the whole resolver and call AttachSession directly via
        # attach() with a stubbed _resolve_target so the eager-open
        # path runs against a real file.
        log = tmp_path / "agent.log"
        log.write_text("")
        with patch.object(
            tail_mod,
            "_resolve_target",
            return_value=_ResolvedTarget(pid=os.getpid(), path=log),
        ):
            sess = attach(os.getpid(), poll_interval_s=0.01)
        with sess:
            with log.open("ab") as fh:
                fh.write(b"world\n")
            seen = _drain_until(sess, lambda b: b"world" in b, timeout=3.0)
            assert b"world" in seen
        assert not sess._thread.is_alive()  # noqa: SLF001


def test_module_thread_cleanup_under_pytest() -> None:
    # Regression guard: leftover threads named "agents-tail-*" indicate
    # an AttachSession was leaked by an earlier test. This test is
    # intentionally placed last in the module so it runs after the
    # rest.
    leaked = [
        t for t in threading.enumerate() if t.name.startswith("agents-tail-") and t.is_alive()
    ]
    assert not leaked, f"leaked tail threads: {[t.name for t in leaked]}"
