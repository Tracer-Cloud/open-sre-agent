"""Lifecycle tests for Buzz polling runtime teardown."""

from __future__ import annotations

import threading
import time
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import MagicMock

from gateway.transports.buzz.runtime import BuzzPollingRuntime, shutdown_buzz_polling_runtime


def test_executor_shutdown_does_not_block_on_in_flight_work() -> None:
    """``stop()`` has an ~8s join budget; waiting on a slow turn would blow it.

    Cancelling the asyncio task does not stop the executor thread. Teardown must
    return without ``wait=True`` on that pool so the Buzz background thread can
    exit inside the gateway stop timeout; unfinished work stays unacked and
    replays on next start.
    """
    started = threading.Event()
    release = threading.Event()
    executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="BuzzTestTurn")
    runtime = BuzzPollingRuntime(
        client=MagicMock(),
        bindings=MagicMock(),
        session_resolver=MagicMock(),
        chat_locks={},
        executor=executor,
    )

    def _slow() -> None:
        started.set()
        release.wait(timeout=30)

    executor.submit(_slow)
    assert started.wait(timeout=2)

    t0 = time.monotonic()
    shutdown_buzz_polling_runtime(runtime)
    elapsed = time.monotonic() - t0

    # Must return immediately — not block for the still-running turn.
    assert elapsed < 1.0
    runtime.bindings.close.assert_called_once()
    release.set()
