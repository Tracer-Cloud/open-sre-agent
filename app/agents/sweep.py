"""Startup orphan / stale-lockfile sweep for the local agent registry.

Run once at REPL boot. Idempotent — running twice in a row is a no-op
the second time. Removes ``AgentRegistry`` entries whose PIDs no longer
exist plus lockfiles in ``~/.config/opensre/agents/`` that correspond
to dead PIDs.

Liveness is checked via ``pid_exists`` rather than ``probe()``
because ``probe()`` returns ``None`` for two distinct reasons —
"PID doesn't exist" and "PID exists but I can't access its fields"
(``psutil.AccessDenied``, common for cross-user processes on macOS
or with a hardened ``/proc``). Treating both cases as "dead" would
silently delete records and lockfiles for live processes owned by
other users. ``pid_exists`` is the right primitive: it does an
OS-level existence check that doesn't traverse the access boundary.

The function is split from the boot wiring so it stays unit-testable
without spinning up the REPL: the loop.py side just calls
``run_startup_sweep()`` and lets this module handle path defaults and
error suppression.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path

from app.agents.probe import pid_exists
from app.agents.registry import AgentRecord, AgentRegistry
from app.constants import OPENSRE_HOME_DIR

logger = logging.getLogger(__name__)

#: Default location of the per-PID lockfile directory.
DEFAULT_LOCK_DIR: Path = OPENSRE_HOME_DIR / "agents"


@dataclass(frozen=True)
class SweepResult:
    """What a single ``sweep()`` invocation removed.

    Empty tuples on an already-clean run; an already-pruned registry
    paired with no stale lockfiles produces ``SweepResult()`` — that's
    the contract idempotency rests on.
    """

    removed_records: tuple[AgentRecord, ...] = field(default_factory=tuple)
    removed_locks: tuple[Path, ...] = field(default_factory=tuple)

    @property
    def total(self) -> int:
        """Sum of removed records and lockfiles. Useful for log messages."""
        return len(self.removed_records) + len(self.removed_locks)


def sweep(
    registry: AgentRegistry,
    lock_dir: Path = DEFAULT_LOCK_DIR,
) -> SweepResult:
    """Remove dead-PID registry entries and stale lockfiles.

    Parameters
    ----------
    registry
        The ``AgentRegistry`` to prune. Mutated in place.
    lock_dir
        Directory containing per-PID lockfiles named ``<pid>.lock``.
        Missing directories are tolerated (returned with empty
        ``removed_locks``); files whose stems aren't integer PIDs are
        left untouched so the sweep can't accidentally delete unrelated
        artifacts.

    Returns
    -------
    SweepResult
        Lists of what was removed. Empty after an already-clean run.
    """
    removed_records = _sweep_registry(registry)
    removed_locks = _sweep_locks(lock_dir)
    return SweepResult(
        removed_records=tuple(removed_records),
        removed_locks=tuple(removed_locks),
    )


def run_startup_sweep() -> SweepResult:
    """Convenience wrapper for the REPL boot path.

    Constructs an ``AgentRegistry`` at the default location, runs
    ``sweep()`` against the default lockfile dir, and swallows any
    exception so a sweep failure never prevents the REPL from
    starting. Returns an empty ``SweepResult`` on error.
    """
    try:
        registry = AgentRegistry()
        result = sweep(registry)
    except Exception:
        # Pinned by ``test_run_startup_sweep_swallows_exceptions`` which
        # mocks ``AgentRegistry()`` to raise. The REPL must boot even if
        # the sweep is broken; logging is the only side effect.
        logger.warning("agent sweep failed at REPL boot", exc_info=True)
        return SweepResult()
    if result.total > 0:
        logger.debug(
            "agent sweep removed %d records and %d lockfiles",
            len(result.removed_records),
            len(result.removed_locks),
        )
    return result


def _sweep_registry(registry: AgentRegistry) -> list[AgentRecord]:
    """Prune dead-PID records in a single batched rewrite.

    Building the dead-PID set first and calling ``forget_many`` once
    means a registry with N dead entries triggers exactly one
    ``_rewrite()`` instead of N — relevant if a developer crashes and
    relaunches the REPL many times without registry maintenance. See
    AgentRegistry.forget_many for the contract.
    """
    dead_pids = [record.pid for record in registry.list() if not pid_exists(record.pid)]
    removed = registry.forget_many(dead_pids)
    for record in removed:
        logger.debug(
            "sweep: forgot dead agent record pid=%s name=%s",
            record.pid,
            record.name,
        )
    return removed


def _sweep_locks(lock_dir: Path) -> list[Path]:
    removed: list[Path] = []
    if not lock_dir.is_dir():
        return removed
    for path in sorted(lock_dir.glob("*.lock")):
        # Filename convention: ``<pid>.lock``. Anything whose stem
        # isn't a valid PID is ignored — we don't know what produced
        # it, and a future naming convention shouldn't trigger
        # spurious removals.
        try:
            pid = int(path.stem)
        except ValueError:
            continue
        if pid_exists(pid):
            continue
        try:
            path.unlink()
        except FileNotFoundError:
            # Race against another sweep / external cleanup that
            # already removed this exact lockfile. Idempotent
            # success — don't log a warning, don't claim we did it.
            continue
        except OSError:
            # Read-only filesystem, permission denied, etc. Log with
            # ``exc_info`` so a downstream operator can tell exactly
            # why; carry on with the rest of the sweep.
            logger.warning(
                "sweep: failed to remove stale lockfile %s (pid=%s)",
                path,
                pid,
                exc_info=True,
            )
            continue
        removed.append(path)
        logger.debug("sweep: removed stale lockfile %s (pid=%s)", path, pid)
    return removed


__all__ = ["DEFAULT_LOCK_DIR", "SweepResult", "run_startup_sweep", "sweep"]
