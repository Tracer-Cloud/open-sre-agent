"""Tests for the Claude Code JSONL token source (#2023)."""

from __future__ import annotations

import time
from pathlib import Path

import psutil
import pytest

from app.fleet_monitoring.token_sources.claude_code import ClaudeCodeJsonlSource


def _mangled_dir(cwd: Path) -> str:
    return str(cwd).replace("/", "-")


@pytest.fixture
def projects_root(tmp_path: Path) -> Path:
    """Isolated ``~/.claude/projects`` per test."""
    root = tmp_path / "claude_projects"
    root.mkdir()
    return root


@pytest.fixture
def source(projects_root: Path) -> ClaudeCodeJsonlSource:
    return ClaudeCodeJsonlSource(projects_root=projects_root)


@pytest.fixture(autouse=True)
def _no_open_files(monkeypatch: pytest.MonkeyPatch) -> None:
    """Default: pretend the test PID has no open files.

    Without this stub, CI runners may have a live process with the
    fake PID (1234) holding fds we can't predict, which would let
    the source's ``_session_file_for_pid`` pick a wrong JSONL.
    Tests that need a specific ``open_files`` set override this.
    """
    monkeypatch.setattr(
        "app.fleet_monitoring.token_sources.claude_code.open_files_for_pid",
        lambda _pid: (),
    )


def _patch_cwd(monkeypatch: pytest.MonkeyPatch, cwd: Path) -> None:
    """Make ``cwd_for_pid`` return ``cwd`` for any PID.

    The source no longer imports ``psutil`` directly (per the #1489
    acceptance criterion that confines psutil to ``app/fleet_monitoring/probe.py``);
    it calls :func:`app.fleet_monitoring.probe.cwd_for_pid`. The test patches
    the helper at the source's import site so the unit test stays
    decoupled from psutil internals.
    """

    monkeypatch.setattr(
        "app.fleet_monitoring.token_sources.claude_code.cwd_for_pid",
        lambda _pid: cwd,
    )


def _patch_open_files(monkeypatch: pytest.MonkeyPatch, *paths: Path) -> None:
    """Make ``open_files_for_pid`` return ``paths`` for any PID.

    The source only attributes a JSONL to a PID when the PID has its
    fd open — the misattribution-prone fallback to ``newest-by-mtime``
    was removed in response to Greptile's review. Tests that expect
    resolution to succeed must declare which session the fake PID is
    "holding".
    """
    monkeypatch.setattr(
        "app.fleet_monitoring.token_sources.claude_code.open_files_for_pid",
        lambda _pid: paths,
    )


def _patch_cwd_raises(monkeypatch: pytest.MonkeyPatch, exc: type[BaseException]) -> None:
    """Simulate the psutil failure path: ``cwd_for_pid`` returns ``None``.

    The ``exc`` parameter is kept for caller-side semantics
    (each test names the kind of failure it intends to model) even
    though ``cwd_for_pid`` collapses them all to ``None`` — exposes
    the exception type in the test name so the failure mode under
    test is greppable.
    """
    del exc  # All psutil failure paths collapse to None via cwd_for_pid.
    monkeypatch.setattr(
        "app.fleet_monitoring.token_sources.claude_code.cwd_for_pid",
        lambda _pid: None,
    )


class TestFirstCallResolution:
    def test_returns_none_when_psutil_denies_cwd(
        self,
        source: ClaudeCodeJsonlSource,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        # macOS hardened-runtime apps can deny ``Process.cwd()`` even
        # cross-user-same-user. The source must degrade silently — the
        # dashboard already renders ``-`` for unobservable PIDs.
        _patch_cwd_raises(monkeypatch, psutil.AccessDenied)
        assert source.read_new_chunk(1234) is None

    def test_returns_none_when_no_such_process(
        self,
        source: ClaudeCodeJsonlSource,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        _patch_cwd_raises(monkeypatch, psutil.NoSuchProcess)
        assert source.read_new_chunk(1234) is None

    def test_returns_none_when_project_dir_missing(
        self,
        source: ClaudeCodeJsonlSource,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
    ) -> None:
        # The cwd resolves but the project dir does not exist yet
        # (claude-code has not written anything). The source must NOT
        # cache an empty resolution — it will resolve next tick when
        # the directory shows up.
        _patch_cwd(monkeypatch, tmp_path / "fresh-project")
        assert source.read_new_chunk(1234) is None

        # Subsequent calls retry; the previous None must not have
        # poisoned the cache.
        assert source.read_new_chunk(1234) is None

    def test_first_call_seeks_to_eof_and_returns_empty_string(
        self,
        source: ClaudeCodeJsonlSource,
        monkeypatch: pytest.MonkeyPatch,
        projects_root: Path,
        tmp_path: Path,
    ) -> None:
        # Attaching to a long-running session should NOT retro-price
        # historical content; the first call seeks to EOF and the
        # next read picks up only new appends.
        cwd = tmp_path / "myrepo"
        cwd.mkdir()
        project_dir = projects_root / _mangled_dir(cwd)
        project_dir.mkdir()
        session = project_dir / "session-abc.jsonl"
        session.write_text("historical line\n" * 50, encoding="utf-8")
        _patch_cwd(monkeypatch, cwd)
        _patch_open_files(monkeypatch, session)

        first = source.read_new_chunk(1234)
        # ``""`` (not None): the PID is now resolved but there is
        # nothing new since cold start.
        assert first == ""

    def test_picks_newest_jsonl_when_pid_holds_multiple(
        self,
        source: ClaudeCodeJsonlSource,
        monkeypatch: pytest.MonkeyPatch,
        projects_root: Path,
        tmp_path: Path,
    ) -> None:
        # A PID can briefly hold fds on two JSONLs (an old session it
        # is finishing flushing, plus the new one it is writing).
        # When multiple fd-matching candidates exist, the source must
        # pick the newest by mtime — the active session — never the
        # stale one.
        cwd = tmp_path / "myrepo"
        cwd.mkdir()
        project_dir = projects_root / _mangled_dir(cwd)
        project_dir.mkdir()
        older = project_dir / "old.jsonl"
        older.write_text("old\n", encoding="utf-8")
        # Force a clearly different mtime so ``max(key=mtime)`` is
        # deterministic regardless of filesystem timestamp granularity.
        old_time = time.time() - 100
        import os as _os

        _os.utime(older, (old_time, old_time))
        newer = project_dir / "new.jsonl"
        newer.write_text("new\n", encoding="utf-8")
        _patch_cwd(monkeypatch, cwd)
        _patch_open_files(monkeypatch, older, newer)

        # First call seeks to EOF on the newest file. We then append
        # to *that* file and confirm the next read returns that
        # appended content (proving the newer file was picked).
        source.read_new_chunk(1234)
        with newer.open("a", encoding="utf-8") as fh:
            fh.write("appended\n")
        assert source.read_new_chunk(1234) == "appended\n"

    def test_returns_none_when_pid_holds_no_jsonl_fd(
        self,
        source: ClaudeCodeJsonlSource,
        monkeypatch: pytest.MonkeyPatch,
        projects_root: Path,
        tmp_path: Path,
    ) -> None:
        # Mirror of the codex source contract: when there is no
        # fd-level evidence that this PID owns a JSONL under the
        # project dir, return ``None`` rather than falling back to a
        # global ``newest-by-mtime`` pick. The old behaviour
        # silently misattributed another session's tokens; the
        # dashboard now honestly renders ``-`` instead.
        cwd = tmp_path / "myrepo"
        cwd.mkdir()
        project_dir = projects_root / _mangled_dir(cwd)
        project_dir.mkdir()
        leftover = project_dir / "someone-elses.jsonl"
        leftover.write_text("not mine\n", encoding="utf-8")
        _patch_cwd(monkeypatch, cwd)
        # ``open_files_for_pid`` returns ``()`` via the autouse
        # fixture — no fds for this PID.

        assert source.read_new_chunk(1234) is None
        # Retry next tick must also stay ``None`` (no poisoned cache).
        assert source.read_new_chunk(1234) is None

    def test_prefers_pid_open_file_when_cwd_has_multiple_sessions(
        self,
        source: ClaudeCodeJsonlSource,
        monkeypatch: pytest.MonkeyPatch,
        projects_root: Path,
        tmp_path: Path,
    ) -> None:
        cwd = tmp_path / "myrepo"
        cwd.mkdir()
        project_dir = projects_root / _mangled_dir(cwd)
        project_dir.mkdir()
        older = project_dir / "older-session.jsonl"
        older.write_text("older historical\n", encoding="utf-8")
        old_time = time.time() - 100
        import os as _os

        _os.utime(older, (old_time, old_time))
        newer = project_dir / "newer-session.jsonl"
        newer.write_text("newer historical\n", encoding="utf-8")
        _patch_cwd(monkeypatch, cwd)
        monkeypatch.setattr(
            "app.fleet_monitoring.token_sources.claude_code.open_files_for_pid",
            lambda pid: (older,) if pid == 1111 else (newer,),
        )

        assert source.read_new_chunk(1111) == ""
        assert source.read_new_chunk(2222) == ""

        with older.open("a", encoding="utf-8") as fh:
            fh.write("older append\n")
        with newer.open("a", encoding="utf-8") as fh:
            fh.write("newer append\n")

        assert source.read_new_chunk(1111) == "older append\n"
        assert source.read_new_chunk(2222) == "newer append\n"


class TestIncrementalReads:
    def test_returns_empty_string_when_nothing_appended(
        self,
        source: ClaudeCodeJsonlSource,
        monkeypatch: pytest.MonkeyPatch,
        projects_root: Path,
        tmp_path: Path,
    ) -> None:
        cwd = tmp_path / "myrepo"
        cwd.mkdir()
        project_dir = projects_root / _mangled_dir(cwd)
        project_dir.mkdir()
        session = project_dir / "session.jsonl"
        session.write_text("initial\n", encoding="utf-8")
        _patch_cwd(monkeypatch, cwd)
        _patch_open_files(monkeypatch, session)

        source.read_new_chunk(1234)
        # Distinct from ``None``: source is observing, nothing new.
        # The wiring layer forwards ``""`` to the meter (returns 0)
        # which records an idle observation.
        assert source.read_new_chunk(1234) == ""

    def test_returns_only_appended_bytes(
        self,
        source: ClaudeCodeJsonlSource,
        monkeypatch: pytest.MonkeyPatch,
        projects_root: Path,
        tmp_path: Path,
    ) -> None:
        cwd = tmp_path / "myrepo"
        cwd.mkdir()
        project_dir = projects_root / _mangled_dir(cwd)
        project_dir.mkdir()
        session = project_dir / "session.jsonl"
        session.write_text("turn1\n", encoding="utf-8")
        _patch_cwd(monkeypatch, cwd)
        _patch_open_files(monkeypatch, session)

        source.read_new_chunk(1234)  # seeks to EOF
        with session.open("a", encoding="utf-8") as fh:
            fh.write("turn2\n")
        assert source.read_new_chunk(1234) == "turn2\n"

        # Second read with no further appends → empty string, not a re-read.
        assert source.read_new_chunk(1234) == ""


class TestRotation:
    def test_inode_change_resets_offset(
        self,
        source: ClaudeCodeJsonlSource,
        monkeypatch: pytest.MonkeyPatch,
        projects_root: Path,
        tmp_path: Path,
    ) -> None:
        # A new claude-code session under the same cwd writes a new
        # JSONL; depending on the user's workflow they may also
        # delete and recreate the same filename. The inode-change
        # heuristic catches both.
        cwd = tmp_path / "myrepo"
        cwd.mkdir()
        project_dir = projects_root / _mangled_dir(cwd)
        project_dir.mkdir()
        session = project_dir / "session.jsonl"
        session.write_text("first session\n", encoding="utf-8")
        _patch_cwd(monkeypatch, cwd)
        _patch_open_files(monkeypatch, session)

        source.read_new_chunk(1234)

        # Delete + recreate with new content. The new inode + smaller
        # file means our cached offset points past EOF; the source
        # must detect this and start over.
        session.unlink()
        session.write_text("rebooted\n", encoding="utf-8")
        # First call after rotation: returns "" because the rotation
        # detector resets state. Next call returns appended bytes.
        assert source.read_new_chunk(1234) == ""

        with session.open("a", encoding="utf-8") as fh:
            fh.write("after-reboot\n")
        assert source.read_new_chunk(1234) == "after-reboot\n"


class TestForget:
    def test_forget_clears_per_pid_state(
        self,
        source: ClaudeCodeJsonlSource,
        monkeypatch: pytest.MonkeyPatch,
        projects_root: Path,
        tmp_path: Path,
    ) -> None:
        cwd = tmp_path / "myrepo"
        cwd.mkdir()
        project_dir = projects_root / _mangled_dir(cwd)
        project_dir.mkdir()
        session = project_dir / "session.jsonl"
        session.write_text("turn1\nturn2\n", encoding="utf-8")
        _patch_cwd(monkeypatch, cwd)
        _patch_open_files(monkeypatch, session)

        source.read_new_chunk(1234)  # cache state, EOF = end of two lines
        source.forget(1234)
        # After forget, the next call resolves fresh. Append before
        # the next call so we can assert resolution worked.
        with session.open("a", encoding="utf-8") as fh:
            fh.write("turn3\n")
        # First call after forget seeks to EOF again (so ``turn3`` is
        # part of the historical content from this PID's view). The
        # contract guarantees no retro-pricing.
        assert source.read_new_chunk(1234) == ""

    def test_forget_unknown_pid_is_silent(self, source: ClaudeCodeJsonlSource) -> None:
        # The sampler GC pass calls ``forget`` on every disappeared
        # PID; it must not raise for PIDs the source never resolved.
        source.forget(99999)
