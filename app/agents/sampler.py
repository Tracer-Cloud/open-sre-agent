from __future__ import annotations

import asyncio
import logging

from app.agents.probe import ProcessSnapshot, probe
from app.agents.registry import AgentRegistry

logger = logging.getLogger(__name__)

# storage for snapshots after every probe tick.
_latest: dict[int, ProcessSnapshot] = {}


def get_snapshot(pid: int) -> ProcessSnapshot | None:
    """Return the most recent probe snapshot for ``pid``, or ``None`` if never sampled."""
    return _latest.get(pid)


async def _sampler_loop(interval: float) -> None:
    """Probe every registered agent each ``interval`` seconds until cancelled.

    Per-PID failures are logged at debug level and do not interrupt
    the loop — a single dead process never tears down the sampler.
    """
    while True:
        registry = AgentRegistry()
        for agent in registry.list():
            try:
                snapshot = await asyncio.get_running_loop().run_in_executor(None, probe, agent.pid)
                if snapshot is not None:
                    _latest[agent.pid] = snapshot
                else:
                    _latest.pop(agent.pid, None)
            except Exception:
                logger.debug("probe failed for pid %d", agent.pid, exc_info=True)
        await asyncio.sleep(interval)


def start_sampler(interval: float = 5.0) -> asyncio.Task[None]:
    """Launch the background sampler and return the cancellable task."""
    return asyncio.create_task(_sampler_loop(interval))
