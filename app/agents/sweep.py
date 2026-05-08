"""Startup orphan / stale-lockfile sweep for the local agent registry.

Run once at REPL boot. Idempotent — running twice in a row is a no-op
the second time. Removes ``AgentRegistry`` entries whose PIDs no longer
exist plus lockfiles in ``~/.config/opensre/agents/`` that correspond
to dead PIDs.

The function is split from the boot wiring so it stays unit-testable
without spinning up the REPL: the loop.py side just calls
``run_startup_sweep()`` and lets this module handle path defaults and
error suppression.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path

from app.agents.probe import probe
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
    except Exception:  # pragma: no cover — defensive boundary
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
    removed: list[AgentRecord] = []
    for record in registry.list():
        # ``cpu_interval=0.0`` because we only need the existence
        # signal, not an accurate CPU sample. Blocking 100 ms per
        # registered agent at boot would be a noticeable startup tax.
        if probe(record.pid, cpu_interval=0.0) is None:
            forgotten = registry.forget(record.pid)
            if forgotten is not None:
                removed.append(forgotten)
                logger.debug(
                    "sweep: forgot dead agent record pid=%s name=%s",
                    forgotten.pid,
                    forgotten.name,
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
        if probe(pid, cpu_interval=0.0) is None:
            try:
                path.unlink()
            except OSError:
                logger.warning("sweep: failed to remove stale lockfile %s", path)
                continue
            removed.append(path)
            logger.debug("sweep: removed stale lockfile %s (pid=%s)", path, pid)
    return removed


__all__ = ["DEFAULT_LOCK_DIR", "SweepResult", "run_startup_sweep", "sweep"]
