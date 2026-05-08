"""Tests for the startup orphan / stale-lockfile sweep (issue #1501)."""

from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import patch

import pytest

import app.agents.sweep as sweep_module
from app.agents.registry import AgentRecord, AgentRegistry
from app.agents.sweep import SweepResult, run_startup_sweep, sweep

# A PID large enough to never be allocated on Linux/macOS
# (``kernel.pid_max`` defaults to 32768 or 4194304). Cross-platform
# Windows would also fail to find this process.
_DEAD_PID = 2**31 - 1


@pytest.fixture
def isolated_registry(tmp_path: Path) -> AgentRegistry:
    """An ``AgentRegistry`` writing to a tmp dir so tests don't touch
    the developer's real ``~/.config/opensre/agents.jsonl``."""
    return AgentRegistry(path=tmp_path / "agents.jsonl")


# ---------------------------------------------------------------------------
# Registry-side sweep
# ---------------------------------------------------------------------------


def test_dead_pid_record_is_forgotten(isolated_registry: AgentRegistry, tmp_path: Path) -> None:
    isolated_registry.register(AgentRecord(name="ghost", pid=_DEAD_PID, command="bin"))
    assert isolated_registry.get(_DEAD_PID) is not None  # sanity: registered ok

    result = sweep(isolated_registry, lock_dir=tmp_path / "no-such-dir")

    assert isolated_registry.get(_DEAD_PID) is None
    assert len(result.removed_records) == 1
    assert result.removed_records[0].pid == _DEAD_PID


def test_live_pid_record_is_kept(isolated_registry: AgentRegistry, tmp_path: Path) -> None:
    """The current Python process is alive; its record must not be pruned."""
    self_pid = os.getpid()
    isolated_registry.register(AgentRecord(name="opensre", pid=self_pid, command="opensre"))

    result = sweep(isolated_registry, lock_dir=tmp_path / "no-such-dir")

    assert isolated_registry.get(self_pid) is not None
    assert result.removed_records == ()


def test_access_denied_pid_is_kept_not_pruned(
    isolated_registry: AgentRegistry, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A live PID owned by another user (where ``psutil.Process(pid)``
    construction would raise ``AccessDenied`` and the old probe-based
    check would falsely return ``None``) must NOT be pruned.

    Locks in the fix for the Copilot review concern: the sweep now
    uses ``psutil.pid_exists`` which doesn't traverse the access
    boundary, so cross-user processes survive both the registry and
    lockfile sweeps.
    """
    foreign_pid = 4242  # arbitrary; we mock pid_exists to say "alive"
    isolated_registry.register(
        AgentRecord(name="other-users-claude", pid=foreign_pid, command="claude")
    )

    monkeypatch.setattr(sweep_module, "pid_exists", lambda pid: pid == foreign_pid)

    result = sweep(isolated_registry, lock_dir=tmp_path / "no-such-dir")

    assert isolated_registry.get(foreign_pid) is not None
    assert result.removed_records == ()


def test_access_denied_pid_lockfile_is_kept(
    isolated_registry: AgentRegistry, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Symmetric to the registry case: a lockfile for a live foreign
    PID must survive the sweep even though the calling process can't
    introspect it. Without the ``pid_exists`` fix, the sweep would
    delete this file and break the foreign agent's coordination.
    """
    foreign_pid = 4242
    lock_dir = tmp_path / "agents"
    lock_dir.mkdir()
    foreign_lock = lock_dir / f"{foreign_pid}.lock"
    foreign_lock.write_text("locked")

    monkeypatch.setattr(sweep_module, "pid_exists", lambda pid: pid == foreign_pid)

    result = sweep(isolated_registry, lock_dir=lock_dir)

    assert foreign_lock.exists()
    assert result.removed_locks == ()


def test_many_dead_pids_trigger_one_rewrite_not_n(
    isolated_registry: AgentRegistry, tmp_path: Path
) -> None:
    """Pruning N dead records must rewrite the JSONL once, not N times.

    Spies on ``AgentRegistry._rewrite`` to count calls — locks in the
    Copilot review concern about boot-time N-rewrite scaling.
    """
    for pid in range(_DEAD_PID - 5, _DEAD_PID):
        isolated_registry.register(AgentRecord(name=f"ghost-{pid}", pid=pid, command="bin"))

    rewrite_call_count = 0
    real_rewrite = isolated_registry._rewrite

    def _spy() -> None:
        nonlocal rewrite_call_count
        rewrite_call_count += 1
        real_rewrite()

    isolated_registry._rewrite = _spy  # type: ignore[method-assign]

    result = sweep(isolated_registry, lock_dir=tmp_path / "no-such-dir")

    assert len(result.removed_records) == 5
    assert rewrite_call_count == 1, (
        f"expected 1 batched rewrite for 5 dead PIDs, got {rewrite_call_count}"
    )


# ---------------------------------------------------------------------------
# Idempotency — the headline acceptance criterion
# ---------------------------------------------------------------------------


def test_idempotent_second_run_is_a_noop(isolated_registry: AgentRegistry, tmp_path: Path) -> None:
    """Running ``sweep`` twice in a row: the second invocation removes
    nothing because the first already cleaned up. This is the contract
    the issue spec leans on for safe boot-time invocation."""
    isolated_registry.register(AgentRecord(name="ghost", pid=_DEAD_PID, command="bin"))
    isolated_registry.register(AgentRecord(name="opensre", pid=os.getpid(), command="opensre"))

    first = sweep(isolated_registry, lock_dir=tmp_path / "no-such-dir")
    second = sweep(isolated_registry, lock_dir=tmp_path / "no-such-dir")

    assert len(first.removed_records) == 1
    assert second.removed_records == ()
    # The live record survives both rounds.
    assert isolated_registry.get(os.getpid()) is not None


# ---------------------------------------------------------------------------
# Lockfile-side sweep
# ---------------------------------------------------------------------------


def test_stale_lockfile_for_dead_pid_is_removed(
    isolated_registry: AgentRegistry, tmp_path: Path
) -> None:
    lock_dir = tmp_path / "agents"
    lock_dir.mkdir()
    stale_lock = lock_dir / f"{_DEAD_PID}.lock"
    stale_lock.write_text("locked")

    result = sweep(isolated_registry, lock_dir=lock_dir)

    assert not stale_lock.exists()
    assert stale_lock in result.removed_locks


def test_live_lockfile_is_kept(isolated_registry: AgentRegistry, tmp_path: Path) -> None:
    lock_dir = tmp_path / "agents"
    lock_dir.mkdir()
    live_lock = lock_dir / f"{os.getpid()}.lock"
    live_lock.write_text("locked")

    result = sweep(isolated_registry, lock_dir=lock_dir)

    assert live_lock.exists()
    assert result.removed_locks == ()


def test_non_pid_lockfile_names_are_ignored(
    isolated_registry: AgentRegistry, tmp_path: Path
) -> None:
    """Files in lock_dir whose stem isn't an integer (foreign tools'
    artifacts, future conventions, etc.) are left untouched. The
    sweep only acts on its own ``<pid>.lock`` shape."""
    lock_dir = tmp_path / "agents"
    lock_dir.mkdir()
    foreign = lock_dir / "registry.lock"
    foreign.write_text("not ours")
    other = lock_dir / "settings.json"
    other.write_text("{}")

    result = sweep(isolated_registry, lock_dir=lock_dir)

    assert foreign.exists()
    assert other.exists()
    assert result.removed_locks == ()


def test_missing_lock_dir_is_tolerated(isolated_registry: AgentRegistry, tmp_path: Path) -> None:
    """A non-existent lock_dir doesn't raise — common on a fresh
    install where no agent has registered yet."""
    result = sweep(isolated_registry, lock_dir=tmp_path / "definitely-not-here")
    assert result.removed_locks == ()


def test_lockfile_unlink_failure_is_logged_with_exc_info(
    isolated_registry: AgentRegistry, tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """If the OS refuses to remove a lockfile (e.g. permission denied
    on a read-only filesystem), the sweep logs at WARNING **with**
    ``exc_info`` so the operator can diagnose the cause, then
    continues rather than crashing the REPL boot.
    """
    lock_dir = tmp_path / "agents"
    lock_dir.mkdir()
    stuck_lock = lock_dir / f"{_DEAD_PID}.lock"
    stuck_lock.write_text("locked")

    with (
        patch.object(Path, "unlink", side_effect=PermissionError("read-only fs")),
        caplog.at_level("WARNING", logger="app.agents.sweep"),
    ):
        result = sweep(isolated_registry, lock_dir=lock_dir)

    # The file is still there because unlink was mocked to fail
    assert stuck_lock.exists()
    # ...but the sweep didn't claim a successful removal
    assert stuck_lock not in result.removed_locks
    # ...and it logged the failure with exception info attached so
    # an ops operator sees *why* the unlink failed, not just *that*
    # it did.
    matching = [r for r in caplog.records if "failed to remove stale lockfile" in r.getMessage()]
    assert matching, "expected a WARNING log for the unlink failure"
    assert matching[0].exc_info is not None, (
        "warning log must include exc_info so the underlying exception is visible"
    )


def test_lockfile_already_gone_is_silently_idempotent(
    isolated_registry: AgentRegistry, tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """A concurrent sweep / external cleanup may have already removed
    the lockfile between our existence check and our unlink call.
    ``FileNotFoundError`` is expected behaviour, not a warning —
    treat it as idempotent success and emit no log line.
    """
    lock_dir = tmp_path / "agents"
    lock_dir.mkdir()
    racy_lock = lock_dir / f"{_DEAD_PID}.lock"
    racy_lock.write_text("locked")

    with (
        patch.object(Path, "unlink", side_effect=FileNotFoundError("already gone")),
        caplog.at_level("WARNING", logger="app.agents.sweep"),
    ):
        result = sweep(isolated_registry, lock_dir=lock_dir)

    # The race-already-removed file is treated as nothing-to-do:
    # not in removed_locks, but also no warning logged.
    assert racy_lock not in result.removed_locks
    assert not [r for r in caplog.records if "failed to remove stale lockfile" in r.getMessage()]


# ---------------------------------------------------------------------------
# SweepResult ergonomics
# ---------------------------------------------------------------------------


def test_sweep_result_total_property(isolated_registry: AgentRegistry, tmp_path: Path) -> None:
    """``SweepResult.total`` is the sum of removed records and locks —
    the single number that's most useful in a debug log line."""
    lock_dir = tmp_path / "agents"
    lock_dir.mkdir()
    isolated_registry.register(AgentRecord(name="ghost", pid=_DEAD_PID, command="bin"))
    (lock_dir / f"{_DEAD_PID}.lock").write_text("locked")
    # ``_DEAD_PID - 1`` to stay within int32 — ``_DEAD_PID`` is the
    # signed-int32 max, so ``+ 1`` would overflow ``psutil.pid_exists``.
    (lock_dir / f"{_DEAD_PID - 1}.lock").write_text("locked")

    result = sweep(isolated_registry, lock_dir=lock_dir)

    assert result.total == 1 + 2  # 1 record + 2 locks


def test_empty_sweep_result_is_falsy_in_total() -> None:
    """A no-op sweep produces ``SweepResult()`` with total == 0 — the
    boot-time logger uses this to decide whether to emit a message."""
    empty = SweepResult()
    assert empty.total == 0
    assert empty.removed_records == ()
    assert empty.removed_locks == ()


# ---------------------------------------------------------------------------
# REPL-boot wrapper
# ---------------------------------------------------------------------------


def test_run_startup_sweep_swallows_exceptions(monkeypatch: pytest.MonkeyPatch) -> None:
    """``run_startup_sweep`` is the REPL's boot hook; an unexpected
    exception inside must NOT propagate, otherwise a sweep bug could
    block the REPL from starting at all."""

    def _explode(*_args: object, **_kwargs: object) -> None:
        raise RuntimeError("simulated registry-load failure")

    monkeypatch.setattr(sweep_module, "AgentRegistry", _explode)

    result = sweep_module.run_startup_sweep()

    assert isinstance(result, SweepResult)
    assert result.total == 0
