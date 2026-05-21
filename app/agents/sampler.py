"""Background sampler that drives the agents dashboard.

Each tick, for every registered or auto-discovered agent:

1. ``probe()`` (psutil) → :class:`ProcessSnapshot` stored in :data:`_latest`.
2. Provider lookup → :class:`TokenSource` read → :class:`TokenMeter`
   parse → :data:`TOKEN_RATE_TRACKER` record.
3. GC pass: any PID not in this tick's live set is forgotten from
   the tracker AND from every source's per-PID cache.

Both steps run in the asyncio executor (file I/O is blocking).
Per-agent failures are swallowed at debug level so a single bad
process never tears down the loop.
"""

from __future__ import annotations

import asyncio
import logging

from app.agents.config import AgentsConfig, load_agents_config
from app.agents.discovery import registered_and_discovered_agents
from app.agents.meters.registry import TOKEN_METER_REGISTRY, get_token_meter
from app.agents.pricing import PriceOverride, normalize_model_name, usd_per_hour_for_usage
from app.agents.probe import ProcessSnapshot, env_value_for_pid, probe
from app.agents.providers import provider_for
from app.agents.registry import AgentRecord, AgentRegistry
from app.agents.token_rate import TOKEN_RATE_TRACKER
from app.agents.token_sources.registry import TOKEN_SOURCE_REGISTRY, get_token_source

logger = logging.getLogger(__name__)

_latest: dict[int, ProcessSnapshot] = {}


class _TickCache:
    """Per-tick caches refreshed by ``_sampler_loop`` so render-time
    lookups don't re-parse disk/discovery state for each row.

    Wrapped in a class (not module-level globals) so the writer
    inside ``_sampler_loop`` can assign attributes without a
    ``global`` declaration — CodeQL flagged the global form as
    "unused" because the reads happen in other functions.
    """

    registry_snapshot: dict[int, AgentRecord] = {}  # noqa: RUF012
    agents_config: AgentsConfig | None = None


_MODEL_ENV_KEYS: dict[str, str] = {
    "claude-code": "CLAUDE_CODE_MODEL",
    "codex": "CODEX_MODEL",
}


def get_snapshot(pid: int) -> ProcessSnapshot | None:
    return _latest.get(pid)


def get_tokens_per_min(pid: int) -> float | None:
    return TOKEN_RATE_TRACKER.tokens_per_min(pid)


def get_usd_per_hour(pid: int) -> float | None:
    return _compute_usd_per_hour(pid)


async def _sampler_loop(interval: float) -> None:
    while True:
        registry = AgentRegistry()
        agents = _records_for_tick(registry)
        # Build the new snapshot locally and swap by reference so the
        # view thread never observes a transient empty cache between
        # ``clear()`` and the first ``__setitem__``.
        _TickCache.registry_snapshot = {record.pid: record for record in agents}
        try:
            _TickCache.agents_config = load_agents_config()
        except Exception:
            logger.debug("failed to load agents.yaml during tick refresh", exc_info=True)
            _TickCache.agents_config = None

        live_pids: set[int] = set()
        for agent in agents:
            live_pids.add(agent.pid)
            try:
                snapshot = await asyncio.get_running_loop().run_in_executor(None, probe, agent.pid)
                if snapshot is not None:
                    _latest[agent.pid] = snapshot
                else:
                    _latest.pop(agent.pid, None)
            except Exception:
                logger.debug("probe failed for pid %d", agent.pid, exc_info=True)
            try:
                await asyncio.get_running_loop().run_in_executor(None, _sample_tokens, agent)
            except Exception:
                logger.debug("token sample failed for pid %d", agent.pid, exc_info=True)
        _gc_disappeared_agents(live_pids)
        await asyncio.sleep(interval)


def _records_for_tick(registry: AgentRegistry) -> list[AgentRecord]:
    try:
        return registered_and_discovered_agents(registry)
    except Exception:
        logger.debug("agent discovery failed during sampler tick", exc_info=True)
        return registry.list()


def _sample_tokens(agent: AgentRecord) -> None:
    provider = provider_for(agent)
    if provider is None:
        return
    source = get_token_source(provider)
    chunk = source.read_new_chunk(agent.pid)
    if chunk is None:
        return
    meter = get_token_meter(provider)
    sample = meter.sample_chunk(chunk, pid=agent.pid)
    # Always record, even with tokens=0, so a known-idle PID renders
    # as ``0`` rather than the never-seen ``-``.
    TOKEN_RATE_TRACKER.record(agent.pid, usage=sample.usage, model=sample.model)


def _gc_disappeared_agents(live_pids: set[int]) -> None:
    """Drop tracker + source state for PIDs no longer in the registry.

    Iterates both the tracker AND each source's own PID cache: a
    source can resolve a PID without the tracker ever recording an
    entry (e.g. a long-idle agent that the meter scores at 0 every
    tick and the wiring layer skips before tracker.record), and
    that state must still be GC'd.
    """
    known = set(TOKEN_RATE_TRACKER.known_pids())
    for source in TOKEN_SOURCE_REGISTRY.values():
        known.update(source.known_pids())
    for meter in TOKEN_METER_REGISTRY.values():
        known.update(meter.known_pids())
    for pid in known - live_pids:
        TOKEN_RATE_TRACKER.forget(pid)
        for source in TOKEN_SOURCE_REGISTRY.values():
            source.forget(pid)
        for meter in TOKEN_METER_REGISTRY.values():
            meter.forget(pid)


def _compute_usd_per_hour(pid: int) -> float | None:
    usage_per_min = TOKEN_RATE_TRACKER.usage_per_min(pid)
    if usage_per_min is None:
        return None
    model = _resolved_model_for_pid(pid)
    override = _price_override_for_pid(pid)
    return usd_per_hour_for_usage(usage_per_min, model, override)


def _price_override_for_pid(pid: int) -> PriceOverride | None:
    config = _TickCache.agents_config
    if config is None:
        return None
    record = _registry_record_for(pid)
    if record is None:
        return None
    budget = config.agents.get(record.name)
    if budget is None:
        return None
    if budget.input_usd_per_million_tokens is None and budget.output_usd_per_million_tokens is None:
        return None
    return PriceOverride(
        input_usd_per_million=budget.input_usd_per_million_tokens,
        output_usd_per_million=budget.output_usd_per_million_tokens,
    )


def _resolved_model_for_pid(pid: int) -> str | None:
    """Three-source model resolution: NDJSON > yaml override > env var.

    NDJSON is most accurate (reflects the live session). The yaml
    override exists for the macOS hardened-runtime case where
    ``psutil.environ()`` is denied even for same-user processes.
    """
    tracked = TOKEN_RATE_TRACKER.latest_model(pid)
    if tracked is not None:
        return normalize_model_name(tracked)

    record = _registry_record_for(pid)
    if record is None:
        return None

    config = _TickCache.agents_config
    if config is not None:
        budget = config.agents.get(record.name)
        if budget is not None and budget.model is not None:
            return normalize_model_name(budget.model)

    provider = provider_for(record)
    if provider is None:
        return None
    env_key = _MODEL_ENV_KEYS.get(provider)
    if env_key is None:
        return None
    return normalize_model_name(env_value_for_pid(pid, env_key))


def _registry_record_for(pid: int) -> AgentRecord | None:
    # The sampler-tick cache is the fast path. Non-interactive
    # callers (e.g. ``opensre agents list``) never enter the loop,
    # so the cache stays empty and we fall back to disk.
    record = _TickCache.registry_snapshot.get(pid)
    if record is not None:
        return record
    if _TickCache.registry_snapshot:
        return None
    return AgentRegistry().get(pid)


def start_sampler(interval: float = 5.0) -> asyncio.Task[None]:
    return asyncio.create_task(_sampler_loop(interval))
