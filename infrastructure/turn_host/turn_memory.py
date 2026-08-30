"""Resident-memory sampling, to size how many turns a task can run at once.

Resident memory is the physical RAM a process is actually using right now — the
number a container's memory limit is enforced against. A turn holds its
conversation and evidence context in RAM for its whole duration, so the ceiling
on concurrent turns is the task's memory divided by the per-turn cost. These
helpers let the turn host log that cost from a real run instead of guessing it.
"""

from __future__ import annotations

import logging
import sys

try:
    import resource as _resource
except ImportError:  # Windows / non-POSIX
    _resource = None  # type: ignore[assignment]

_BYTES_PER_MB = 1_048_576


def resident_memory_bytes() -> int | None:
    """This process's current resident memory in bytes, or ``None`` if unknown.

    Reads ``/proc/self/statm`` for an accurate live reading on Linux (the Fargate
    runtime); returns ``None`` where the required platform APIs are absent so
    callers skip the measurement rather than report a wrong number.
    """
    if _resource is None:
        return None
    try:
        with open("/proc/self/statm", encoding="ascii") as statm:
            resident_pages = int(statm.read().split()[1])
    except (OSError, ValueError, IndexError):
        return None
    return resident_pages * _resource.getpagesize()


def peak_resident_memory_bytes() -> int | None:
    """This process's peak resident memory in bytes, or ``None`` if unknown."""
    if _resource is None:
        return None
    try:
        peak = _resource.getrusage(_resource.RUSAGE_SELF).ru_maxrss
    except (OSError, ValueError):
        return None
    # Linux reports kibibytes; macOS and the BSDs report bytes.
    return peak if sys.platform == "darwin" else peak * 1024


def log_turn_memory(logger: logging.Logger, memory_before: int | None) -> None:
    """Log one turn's resident-memory cost, given the reading taken before it.

    A no-op where resident memory is unavailable (e.g. macOS dev has no
    ``/proc``), so the caller need not guard. The concurrency ceiling for a task
    is roughly its memory divided by this per-turn cost.
    """
    memory_after = resident_memory_bytes()
    if memory_before is None or memory_after is None:
        return
    peak = peak_resident_memory_bytes()
    logger.debug(
        "gateway_turn_memory start_mb=%.1f end_mb=%.1f delta_mb=%.1f peak_mb=%s",
        memory_before / _BYTES_PER_MB,
        memory_after / _BYTES_PER_MB,
        (memory_after - memory_before) / _BYTES_PER_MB,
        f"{peak / _BYTES_PER_MB:.1f}" if peak is not None else "unknown",
    )


__all__ = ["log_turn_memory", "peak_resident_memory_bytes", "resident_memory_bytes"]
