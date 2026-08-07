"""Reaping a cancelled asyncio task must still await its cleanup."""

from __future__ import annotations

import asyncio

from gateway.transports.discord.worker import _reap_cancelled_task


def test_reap_cancelled_task_awaits_cleanup() -> None:
    async def _scenario() -> None:
        cleaned = asyncio.Event()

        async def _work() -> None:
            try:
                await asyncio.sleep(3600)
            finally:
                cleaned.set()

        task: asyncio.Task[None] = asyncio.create_task(_work())
        await asyncio.sleep(0)  # let _work reach the sleep
        task.cancel()
        await _reap_cancelled_task(task)
        assert cleaned.is_set()
        assert task.done()

    asyncio.run(_scenario())


def test_reap_cancelled_task_accepts_already_finished_task() -> None:
    async def _scenario() -> None:
        async def _done() -> str:
            return "ok"

        task: asyncio.Task[str] = asyncio.create_task(_done())
        await asyncio.sleep(0)
        await _reap_cancelled_task(task)
        assert task.result() == "ok"

    asyncio.run(_scenario())
