"""Resident-memory sampling, to size how many turns a task can run at once.

A turn holds its conversation and evidence context in memory for its whole
duration, so the ceiling on concurrent turns is the task's memory divided by
the per-turn cost. These helpers let the turn host log that cost from a real
run instead of guessing it.
"""

from __future__ import annotations

import resource
import sys


def current_rss_bytes() -> int | None:
    """This process's current resident set size in bytes, or ``None`` if unknown.

    Reads ``/proc/self/statm`` for an accurate current RSS on Linux (the Fargate
    runtime); returns ``None`` where that file is absent so callers skip the
    measurement rather than report a wrong number.
    """
    try:
        with open("/proc/self/statm", encoding="ascii") as statm:
            resident_pages = int(statm.read().split()[1])
    except (OSError, ValueError, IndexError):
        return None
    return resident_pages * resource.getpagesize()


def peak_rss_bytes() -> int | None:
    """This process's peak resident set size in bytes, or ``None`` if unknown."""
    try:
        peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    except (OSError, ValueError):
        return None
    # Linux reports kibibytes; macOS and the BSDs report bytes.
    return peak if sys.platform == "darwin" else peak * 1024


__all__ = ["current_rss_bytes", "peak_rss_bytes"]
