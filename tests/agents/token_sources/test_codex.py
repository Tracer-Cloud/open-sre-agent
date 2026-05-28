"""Tests for the Codex rollout token source (#2023)."""

from __future__ import annotations

import time
from datetime import UTC, datetime
from pathlib import Path

import psutil
import pytest

from app.agents.token_sources.codex import CodexRolloutSource


@pytest.fixture
def codex_home(tmp_path: Path) -> Path:
    """Isolated ``$CODEX_HOME`` per test."""
    home = tmp_path / "codex_home"
    home.mkdir()
    return home


@pytest.fixture
def source(codex_home: Path) -> CodexRolloutSource:
    return CodexRolloutSource(codex_home=codex_home)


@pytest.fixture(autouse=True)
def _default_open_rollouts(monkeypatch: pytest.MonkeyPatch, codex_home: Path) -> None:
    """Default stub: treat every rollout under the test codex_home as
    "open by this PID". Tests that need a narrower mock override.
    """

    def _all_rollouts(_pid: int) -> tuple[Path, ...]:
        if not codex_home.is_dir():
            return ()
        return tuple(codex_home.rglob("rollout-*.jsonl"))

    monkeypatch.setattr("app.agents.token_sources.codex.open_files_for_pid", _all_rollouts)


def _patch_create_time(monkeypatch: pytest.MonkeyPatch, started_at_epoch: float) -> None:
    """Stub :func:`started_at_for_pid` at the source's import site.

    The source uses :func:`app.agents.probe.started_at_for_pid` (the
    psutil-fenced helper) rather than ``psutil.Process`` directly, so
    the unit test patches that helper.
    """
    monkeypatch.setattr(
        "app.agents.token_sources.codex.started_at_for_pid",
        lambda _pid: started_at_epoch,
    )


def _patch_create_time_raises(monkeypatch: pytest.MonkeyPatch, exc: type[BaseException]) -> None:
    """Simulate the psutil failure path: ``started_at_for_pid`` returns ``None``.

    ``exc`` is documentation-only — the helper collapses every
    psutil failure to ``None`` — but keeping the parameter makes the
    test names match the failure type they intend to model.
    """
    del exc
    monkeypatch.setattr(
        "app.agents.token_sources.codex.started_at_for_pid",
        lambda _pid: None,
    )


def _rollout_dir(codex_home: Path, dt: datetime) -> Path:
    path = codex_home / "sessions" / dt.strftime("%Y") / dt.strftime("%m") / dt.strftime("%d")
    path.mkdir(parents=True, exist_ok=True)
    return path


def _write_rollout(directory: Path, name: str, content: str, mtime: float) -> Path:
    import os as _os

    path = directory / name
    path.write_text(content, encoding="utf-8")
    _os.utime(path, (mtime, mtime))
    return path


class TestFirstCallResolution:
    def test_returns_none_when_psutil_denies_create_time(
        self,
        source: CodexRolloutSource,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        _patch_create_time_raises(monkeypatch, psutil.AccessDenied)
        assert source.read_new_chunk(1234) is None

    def test_returns_none_when_no_rollout_for_pid(
        self,
        source: CodexRolloutSource,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        # ``$CODEX_HOME/sessions/`` does not exist or is empty —
        # codex agent registered before its first turn writes a
        # rollout. The source must NOT cache None so the next tick
        # can find the file once codex flushes.
        _patch_create_time(monkeypatch, time.time())
        assert source.read_new_chunk(1234) is None
        assert source.read_new_chunk(1234) is None

    def test_picks_newest_rollout_open_by_pid(
        self,
        source: CodexRolloutSource,
        codex_home: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        # The PID has fds on two rollouts (rare but possible during
        # a session crash + restart); pick the newer.
        now = datetime.now(tz=UTC)
        started_at = now.timestamp() - 60
        rollout_dir = _rollout_dir(codex_home, now)
        older = _write_rollout(rollout_dir, "rollout-001.jsonl", "older\n", mtime=started_at + 10)
        newer = _write_rollout(rollout_dir, "rollout-002.jsonl", "newer\n", mtime=started_at + 50)
        _patch_create_time(monkeypatch, started_at)
        monkeypatch.setattr(
            "app.agents.token_sources.codex.open_files_for_pid",
            lambda _pid: (older, newer),
        )

        assert source.read_new_chunk(1234) == ""
        with newer.open("a", encoding="utf-8") as fh:
            fh.write("after\n")
        assert source.read_new_chunk(1234) == "after\n"

    def test_returns_none_when_pid_holds_no_rollout_fd(
        self,
        source: CodexRolloutSource,
        codex_home: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        # A codex helper that doesn't write the rollout (or a codex
        # transient between writes) must not be attributed another
        # session's tokens. The dashboard renders ``-`` for it.
        now = datetime.now(tz=UTC)
        started_at = now.timestamp() - 60
        rollout_dir = _rollout_dir(codex_home, now)
        _write_rollout(rollout_dir, "rollout-other.jsonl", "other session\n", mtime=started_at + 50)
        _patch_create_time(monkeypatch, started_at)
        monkeypatch.setattr(
            "app.agents.token_sources.codex.open_files_for_pid",
            lambda _pid: (),
        )

        assert source.read_new_chunk(1234) is None

    def test_prefers_rollout_open_by_pid_over_newest(
        self,
        source: CodexRolloutSource,
        codex_home: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        # Two concurrent codex processes share a date directory; the
        # source must resolve each PID to *its own* rollout, not the
        # global newest. ``open_files_for_pid`` is the disambiguator.
        now = datetime.now(tz=UTC)
        started_at = now.timestamp() - 60
        rollout_dir = _rollout_dir(codex_home, now)
        mine = _write_rollout(rollout_dir, "rollout-mine.jsonl", "mine\n", mtime=started_at + 10)
        _write_rollout(rollout_dir, "rollout-others.jsonl", "others\n", mtime=started_at + 50)
        _patch_create_time(monkeypatch, started_at)
        monkeypatch.setattr(
            "app.agents.token_sources.codex.open_files_for_pid",
            lambda _pid: (mine,),
        )

        assert source.read_new_chunk(1234) == ""  # seek to mine's EOF
        with mine.open("a", encoding="utf-8") as fh:
            fh.write("after-mine\n")
        assert source.read_new_chunk(1234) == "after-mine\n"

    def test_filters_out_rollouts_older_than_process_start(
        self,
        source: CodexRolloutSource,
        codex_home: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        # A rollout that pre-dates the process by more than the 5 s
        # mtime slack belongs to a *different* codex invocation. The
        # source must filter it out so the wrong PID's content
        # cannot be retro-priced against the new PID.
        now = datetime.now(tz=UTC)
        started_at = now.timestamp()
        rollout_dir = _rollout_dir(codex_home, now)
        _write_rollout(
            rollout_dir,
            "rollout-stale.jsonl",
            "from another session\n",
            mtime=started_at - 1000,
        )
        _patch_create_time(monkeypatch, started_at)

        # Stale rollout is the only file; filter excludes it, so the
        # source returns None (will retry next tick).
        assert source.read_new_chunk(1234) is None


class TestIncrementalReads:
    def test_appended_bytes_are_returned(
        self,
        source: CodexRolloutSource,
        codex_home: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        now = datetime.now(tz=UTC)
        started_at = now.timestamp() - 10
        rollout_dir = _rollout_dir(codex_home, now)
        rollout = _write_rollout(
            rollout_dir,
            "rollout-001.jsonl",
            '{"type":"turn.started"}\n',
            mtime=started_at + 1,
        )
        _patch_create_time(monkeypatch, started_at)

        assert source.read_new_chunk(1234) == ""  # seek to EOF
        with rollout.open("a", encoding="utf-8") as fh:
            fh.write('{"type":"turn.completed","usage":{"input_tokens":10,"output_tokens":5}}\n')
        chunk = source.read_new_chunk(1234)
        assert chunk is not None
        assert "turn.completed" in chunk
        assert "10" in chunk


class TestMidnightBoundary:
    def test_today_and_tomorrow_dirs_both_scanned(
        self,
        source: CodexRolloutSource,
        codex_home: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        # A process started at 23:59:58 on day N may have its first
        # rollout flushed at 00:00:01 on day N+1 — Codex partitions
        # by rollout creation time, not process start. The source
        # must scan tomorrow's dir too.
        # Build a "yesterday" started_at relative to 'today' so the
        # rollout we drop in today's dir is the cross-midnight case.
        now = datetime.now(tz=UTC)
        # Pick a fake started_at one hour before now so 'tomorrow'
        # relative to it is still 'today' from the test's POV when
        # date(now) == date(started_at). To exercise the midnight
        # path we artificially scope the rollout in the *next-day*
        # dir from started_at's perspective.
        from datetime import timedelta as _td

        started_at_dt = now - _td(days=1, seconds=1)
        started_at = started_at_dt.timestamp()
        # rollout lives in *today's* dir (started_at's "tomorrow").
        rollout_dir = _rollout_dir(codex_home, started_at_dt + _td(days=1))
        rollout = _write_rollout(
            rollout_dir,
            "rollout-cross-midnight.jsonl",
            "first event\n",
            mtime=started_at + 30,
        )
        _patch_create_time(monkeypatch, started_at)

        assert source.read_new_chunk(1234) == ""
        with rollout.open("a", encoding="utf-8") as fh:
            fh.write("after\n")
        assert source.read_new_chunk(1234) == "after\n"


class TestForget:
    def test_forget_clears_state(
        self,
        source: CodexRolloutSource,
        codex_home: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        now = datetime.now(tz=UTC)
        started_at = now.timestamp() - 10
        rollout_dir = _rollout_dir(codex_home, now)
        _write_rollout(rollout_dir, "rollout-001.jsonl", "x\n", mtime=started_at + 1)
        _patch_create_time(monkeypatch, started_at)

        source.read_new_chunk(1234)
        source.forget(1234)
        # After forget, state is gone; next call re-resolves
        # (validated indirectly by it not raising and returning a
        # valid empty/resolved response).
        assert source.read_new_chunk(1234) == ""

    def test_forget_unknown_pid_is_silent(self, source: CodexRolloutSource) -> None:
        source.forget(99999)


class TestCodexHomeOverride:
    def test_env_var_codex_home_is_honored(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        # Default-constructed source (no explicit ``codex_home``) must
        # read ``CODEX_HOME`` lazily at first resolve, so tests can
        # set it after construction.
        custom_home = tmp_path / "custom_codex"
        custom_home.mkdir()
        monkeypatch.setenv("CODEX_HOME", str(custom_home))

        source = CodexRolloutSource()  # no explicit override

        now = datetime.now(tz=UTC)
        started_at = now.timestamp() - 10
        rollout_dir = (
            custom_home / "sessions" / now.strftime("%Y") / now.strftime("%m") / now.strftime("%d")
        )
        rollout_dir.mkdir(parents=True)
        rollout = _write_rollout(rollout_dir, "rollout-001.jsonl", "x\n", mtime=started_at + 1)
        _patch_create_time(monkeypatch, started_at)
        # Test uses ``custom_home`` instead of the autouse ``codex_home``
        # fixture, so override ``open_files_for_pid`` to point at the
        # rollout actually written here.
        monkeypatch.setattr(
            "app.agents.token_sources.codex.open_files_for_pid",
            lambda _pid: (rollout,),
        )

        assert source.read_new_chunk(1234) == ""
