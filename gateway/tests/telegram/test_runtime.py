"""Lifecycle tests for Telegram polling runtime teardown."""

from __future__ import annotations

import threading
import time
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import MagicMock

from gateway.core.middleware.active_turns import ActiveTurnRegistry
from gateway.core.middleware.approvals import ApprovalBroker
from gateway.transports.telegram.runtime import (
    TelegramPollingRuntime,
    shutdown_telegram_polling_runtime,
)


def test_executor_shutdown_does_not_block_on_in_flight_work() -> None:
    """``stop()`` has an ~8s join budget; waiting on a slow turn would blow it.

    Regression for #5076: cancelling the asyncio task wrapping a dispatched
    turn does not stop the executor thread underneath it. Teardown must
    return without ``wait=True`` on that pool so the Telegram background
    thread can exit inside the gateway stop timeout, matching the Buzz and
    Discord transports' own ``wait=False`` shutdown.
    """
    started = threading.Event()
    release = threading.Event()
    executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="TelegramTestTurn")
    runtime = TelegramPollingRuntime(
        client=MagicMock(),
        bindings=MagicMock(),
        session_resolver=MagicMock(),
        chat_locks={},
        executor=executor,
        approvals=ApprovalBroker(),
        active_cancels=ActiveTurnRegistry(),
    )

    def _slow() -> None:
        started.set()
        release.wait(timeout=30)

    executor.submit(_slow)
    assert started.wait(timeout=2)

    t0 = time.monotonic()
    shutdown_telegram_polling_runtime(runtime)
    elapsed = time.monotonic() - t0

    # Must return immediately — not block for the still-running turn.
    assert elapsed < 1.0
    runtime.bindings.close.assert_called_once()
    release.set()
