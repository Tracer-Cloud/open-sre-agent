"""Generic polling helper for tests that need to wait on an async condition."""

from __future__ import annotations

import time
from collections.abc import Callable


def wait_until(
    predicate: Callable[[], bool],
    *,
    timeout: float,
    interval: float = 0.05,
) -> None:
    """Poll ``predicate`` until it returns true or ``timeout`` elapses.

    Args:
        predicate: Zero-arg callable returning True once the awaited
            condition is satisfied.
        timeout: Maximum seconds to poll before raising.
        interval: Seconds to sleep between polls.

    Raises:
        TimeoutError: If ``predicate`` never returns true within ``timeout``.
    """
    deadline = time.monotonic() + timeout
    while True:
        if time.monotonic() >= deadline:
            raise TimeoutError(f"wait_until: predicate not satisfied within {timeout}s")
        if predicate():
            return
        remaining = deadline - time.monotonic()
        time.sleep(min(interval, remaining) if remaining > 0 else 0)
