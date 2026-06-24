from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from pathlib import Path

import pytest

from app.fleet_monitoring.meters import TokenSample, TokenUsage
from app.fleet_monitoring.probe import ProcessSnapshot
from app.fleet_monitoring.registry import AgentRecord, AgentRegistry
from app.fleet_monitoring.sampler import (
    _latest,
    get_snapshot,
    get_tokens_per_min,
    get_usd_per_hour,
    start_sampler,
)
from app.fleet_monitoring.token_rate import TOKEN_RATE_TRACKER


@pytest.fixture
def registry(tmp_path: Path) -> AgentRegistry:
    return AgentRegistry(path=tmp_path / "agents.jsonl")


@pytest.fixture
def fake_snapshot() -> ProcessSnapshot:
    return ProcessSnapshot(
        pid=8421,
        cpu_percent=23.5,
        rss_mb=128.0,
        num_fds=42,
        num_connections=3,
        status="running",
        started_at=datetime(2026, 5, 10, 12, 0, 0, tzinfo=UTC),
    )


@pytest.fixture(autouse=True)
def _clear_sampler_state(monkeypatch: pytest.MonkeyPatch) -> None:
    """Reset module-level state between tests.

    Also resets the global token rate tracker so a test from #2023
    cannot leak per-PID entries into a peer test.
    """
    _latest.clear()
    # Forget every PID the tracker still knows about so per-test
    # state stays isolated.
    for pid in list(TOKEN_RATE_TRACKER.known_pids()):
        TOKEN_RATE_TRACKER.forget(pid)
    monkeypatch.setattr(
        "app.fleet_monitoring.sampler._records_for_tick",
        lambda registry: registry.list(),
    )


class _FakeSource:
    """Test double for :class:`TokenSource`. Returns a queue of chunks.

    Once the queued chunks are exhausted, subsequent reads repeat the
    *last* enqueued value indefinitely. This matters because the
    sampler runs many ticks during a short ``asyncio.sleep``, and
    the test wants to assert a stable per-source behavior — not a
    one-shot result that decays into a different code path on later
    ticks.
    """

    def __init__(self, chunks: list[str | None]) -> None:
        if not chunks:
            raise ValueError("at least one chunk required")
        self._chunks = list(chunks)
        self.forgotten: list[int] = []

    def read_new_chunk(self, pid: int) -> str | None:  # noqa: ARG002
        if len(self._chunks) > 1:
            return self._chunks.pop(0)
        # Last entry sticks — every subsequent tick returns the same
        # value so the test's assertion observes the steady state.
        return self._chunks[0]

    def forget(self, pid: int) -> None:
        self.forgotten.append(pid)


class _FakeMeter:
    """Test double for :class:`TokenMeter`. Maps chunk → fixed sample."""

    def __init__(self, mapping: dict[str, TokenSample]) -> None:
        self._mapping = mapping

    def parse_chunk(self, chunk: str) -> int:
        return self._mapping.get(chunk, TokenSample()).tokens

    def sample_chunk(self, chunk: str, *, pid: int | None = None) -> TokenSample:  # noqa: ARG002
        return self._mapping.get(chunk, TokenSample())

    def forget(self, _pid: int) -> None:
        return None

    def known_pids(self) -> list[int]:
        return []


@pytest.mark.asyncio
async def test_sampler_stores_snapshots(
    monkeypatch: pytest.MonkeyPatch,
    registry: AgentRegistry,
    fake_snapshot: ProcessSnapshot,
) -> None:
    """Sampler probes registered agents and stores snapshots."""
    registry.register(
        AgentRecord(
            name="claude-code",
            pid=8421,
            command="claude --dangerously-skip-permissions",
            registered_at="2026-05-07T12:00:00+00:00",
        )
    )
    monkeypatch.setattr("app.fleet_monitoring.sampler.AgentRegistry", lambda: registry)

    monkeypatch.setattr("app.fleet_monitoring.sampler.probe", lambda _pid: fake_snapshot)

    task = start_sampler(interval=0.01)
    await asyncio.sleep(0.05)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        _ = await task

    assert get_snapshot(fake_snapshot.pid) == fake_snapshot


@pytest.mark.asyncio
async def test_none_probe_does_not_store(
    monkeypatch: pytest.MonkeyPatch,
    registry: AgentRegistry,
) -> None:
    """When probe returns None, no snapshot is stored."""
    registry.register(AgentRecord(name="dead-agent", pid=9999, command="bin"))
    monkeypatch.setattr("app.fleet_monitoring.sampler.AgentRegistry", lambda: registry)

    monkeypatch.setattr("app.fleet_monitoring.sampler.probe", lambda _pid: None)

    task = start_sampler(interval=0.01)
    await asyncio.sleep(0.05)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        _ = await task

    assert get_snapshot(9999) is None


@pytest.mark.asyncio
async def test_one_pid_failure_does_not_crash_loop(
    monkeypatch: pytest.MonkeyPatch,
    registry: AgentRegistry,
    fake_snapshot: ProcessSnapshot,
) -> None:
    """A failing probe for one PID doesn't prevent probing others."""
    registry.register(AgentRecord(name="crasher", pid=1111, command="bin"))
    registry.register(AgentRecord(name="healthy", pid=8421, command="claude"))
    monkeypatch.setattr("app.fleet_monitoring.sampler.AgentRegistry", lambda: registry)

    def mock_probe(pid: int) -> ProcessSnapshot | None:
        if pid == 1111:
            raise RuntimeError("simulated psutil failure")
        return fake_snapshot

    monkeypatch.setattr("app.fleet_monitoring.sampler.probe", mock_probe)

    task = start_sampler(interval=0.01)
    await asyncio.sleep(0.05)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        _ = await task

    # The healthy agent was still probed despite the crasher
    assert get_snapshot(8421) == fake_snapshot
    # The crasher has no snapshot
    assert get_snapshot(1111) is None


@pytest.mark.asyncio
async def test_sampler_cancels_cleanly(
    monkeypatch: pytest.MonkeyPatch,
    registry: AgentRegistry,
) -> None:
    """Cancelling the sampler task raises CancelledError and nothing else."""
    registry.register(AgentRecord(name="agent", pid=1234, command="bin"))
    monkeypatch.setattr("app.fleet_monitoring.sampler.AgentRegistry", lambda: registry)

    monkeypatch.setattr("app.fleet_monitoring.sampler.probe", lambda _pid: None)

    task = start_sampler(interval=0.01)
    await asyncio.sleep(0.02)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        _ = await task


@pytest.mark.asyncio
async def test_stale_snapshot_evicted_when_probe_returns_none(
    monkeypatch: pytest.MonkeyPatch,
    registry: AgentRegistry,
    fake_snapshot: ProcessSnapshot,
) -> None:
    """A previously stored snapshot is evicted when probe returns None."""
    _latest[8421] = fake_snapshot

    assert get_snapshot(8421) == fake_snapshot

    registry.register(
        AgentRecord(
            name="claude-code",
            pid=8421,
            command="claude --dangerously-skip-permissions",
            registered_at="2026-05-07T12:00:00+00:00",
        )
    )
    monkeypatch.setattr("app.fleet_monitoring.sampler.AgentRegistry", lambda: registry)

    monkeypatch.setattr("app.fleet_monitoring.sampler.probe", lambda _pid: None)

    task = start_sampler(interval=0.01)
    await asyncio.sleep(0.02)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        _ = await task

    assert get_snapshot(8421) is None


# ----- #2023: token-source / meter / tracker wiring ------------------------


def _stub_token_pipeline(
    monkeypatch: pytest.MonkeyPatch,
    *,
    provider: str,
    source: _FakeSource,
    meter: _FakeMeter,
) -> None:
    """Wire fakes into the sampler's import-site lookups.

    Patches the three functions :func:`_sample_tokens` calls so each
    test can swap in a deterministic source/meter pair without
    touching the disk or psutil.
    """
    monkeypatch.setattr("app.fleet_monitoring.sampler.provider_for", lambda _record: provider)
    monkeypatch.setattr("app.fleet_monitoring.sampler.get_token_source", lambda _provider: source)
    monkeypatch.setattr("app.fleet_monitoring.sampler.get_token_meter", lambda _provider: meter)


@pytest.mark.asyncio
async def test_sampler_records_tokens_when_meter_returns_positive(
    monkeypatch: pytest.MonkeyPatch,
    registry: AgentRegistry,
    fake_snapshot: ProcessSnapshot,
) -> None:
    """End-to-end of the token path: source emits a chunk, meter
    parses tokens + model, tracker records the entry, accessor
    returns the rate.
    """
    registry.register(AgentRecord(name="claude-code-8421", pid=8421, command="claude"))
    monkeypatch.setattr("app.fleet_monitoring.sampler.AgentRegistry", lambda: registry)
    monkeypatch.setattr("app.fleet_monitoring.sampler.probe", lambda _pid: fake_snapshot)

    source = _FakeSource(chunks=["chunk-1"])
    meter = _FakeMeter({"chunk-1": TokenSample.from_tokens(200, model="claude-sonnet-4-5")})
    _stub_token_pipeline(monkeypatch, provider="claude-code", source=source, meter=meter)

    task = start_sampler(interval=0.01)
    await asyncio.sleep(0.05)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        _ = await task

    # Tracker recorded the 200-token chunk; tokens_per_min reflects
    # the window-scaled rate (one observation in a 60 s window).
    assert get_tokens_per_min(8421) is not None
    assert get_tokens_per_min(8421) >= 200.0
    # $/hr derives from tokens_per_min × 60 × blended-rate; a known
    # model yields a positive figure rather than None.
    cost = get_usd_per_hour(8421)
    assert cost is not None
    assert cost > 0.0


def test_get_usd_per_hour_uses_structured_usage_buckets() -> None:
    TOKEN_RATE_TRACKER.record(
        8421,
        usage=TokenUsage(input_tokens=1000, cached_input_tokens=250, output_tokens=100),
        model="gpt-5-codex",
    )

    expected_per_min = (750 * 1.25e-6) + (250 * 0.125e-6) + (100 * 10e-6)
    assert get_usd_per_hour(8421) == pytest.approx(expected_per_min * 60.0)


@pytest.mark.asyncio
async def test_sampler_records_tokens_for_auto_discovered_agents(
    monkeypatch: pytest.MonkeyPatch,
    registry: AgentRegistry,
    fake_snapshot: ProcessSnapshot,
) -> None:
    """The sampler must cover the same discovered rows that ``/fleet`` renders."""
    discovered = AgentRecord(
        name="codex",
        pid=9999,
        command="codex exec --ephemeral",
        source="discovered",
        provider="codex",
    )
    monkeypatch.setattr("app.fleet_monitoring.sampler.AgentRegistry", lambda: registry)
    monkeypatch.setattr(
        "app.fleet_monitoring.sampler._records_for_tick", lambda _registry: [discovered]
    )
    monkeypatch.setattr("app.fleet_monitoring.sampler.probe", lambda _pid: fake_snapshot)

    source = _FakeSource(chunks=["chunk-1"])
    meter = _FakeMeter({"chunk-1": TokenSample.from_tokens(123, model="gpt-5-codex")})
    _stub_token_pipeline(monkeypatch, provider="codex", source=source, meter=meter)

    task = start_sampler(interval=0.01)
    await asyncio.sleep(0.05)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        _ = await task

    assert get_tokens_per_min(9999) is not None
    assert get_tokens_per_min(9999) >= 123.0


@pytest.mark.asyncio
async def test_sampler_does_not_record_when_source_returns_none(
    monkeypatch: pytest.MonkeyPatch,
    registry: AgentRegistry,
    fake_snapshot: ProcessSnapshot,
) -> None:
    """``None`` from the source means "cannot observe this PID";
    the tracker must stay empty so the view renders ``-``, not ``0``.
    """
    registry.register(AgentRecord(name="claude-code-8421", pid=8421, command="claude"))
    monkeypatch.setattr("app.fleet_monitoring.sampler.AgentRegistry", lambda: registry)
    monkeypatch.setattr("app.fleet_monitoring.sampler.probe", lambda _pid: fake_snapshot)

    source = _FakeSource(chunks=[None])
    meter = _FakeMeter({})
    _stub_token_pipeline(monkeypatch, provider="claude-code", source=source, meter=meter)

    task = start_sampler(interval=0.01)
    await asyncio.sleep(0.05)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        _ = await task

    # Tracker never recorded an entry; view sees None → renders ``-``.
    assert get_tokens_per_min(8421) is None
    assert get_usd_per_hour(8421) is None


@pytest.mark.asyncio
async def test_one_token_source_failure_does_not_crash_loop(
    monkeypatch: pytest.MonkeyPatch,
    registry: AgentRegistry,
    fake_snapshot: ProcessSnapshot,
) -> None:
    """The #2023 counterpart of ``test_one_pid_failure_does_not_crash_loop``:
    a raising token-source on one PID must not prevent token sampling
    for healthy peers.
    """
    registry.register(AgentRecord(name="crasher", pid=1111, command="bin"))
    registry.register(AgentRecord(name="healthy", pid=8421, command="claude"))
    monkeypatch.setattr("app.fleet_monitoring.sampler.AgentRegistry", lambda: registry)
    monkeypatch.setattr("app.fleet_monitoring.sampler.probe", lambda _pid: fake_snapshot)

    class _CrashingSource:
        def __init__(self) -> None:
            self.calls: list[int] = []

        def read_new_chunk(self, pid: int) -> str | None:
            self.calls.append(pid)
            if pid == 1111:
                raise RuntimeError("simulated source failure")
            return "ok"

        def forget(self, pid: int) -> None:  # noqa: ARG002
            pass

    source = _CrashingSource()
    meter = _FakeMeter({"ok": TokenSample.from_tokens(42, model="claude-sonnet-4-5")})
    _stub_token_pipeline(monkeypatch, provider="claude-code", source=source, meter=meter)

    task = start_sampler(interval=0.01)
    await asyncio.sleep(0.05)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        _ = await task

    # Healthy PID was observed despite the crasher.
    assert get_tokens_per_min(8421) is not None
    # Crasher has no tracker entry (its read failed).
    assert get_tokens_per_min(1111) is None


@pytest.mark.asyncio
async def test_disappeared_agent_state_is_garbage_collected(
    monkeypatch: pytest.MonkeyPatch,
    registry: AgentRegistry,
    fake_snapshot: ProcessSnapshot,
) -> None:
    """Once a PID is removed from the registry, the next sampler tick
    drops its tracker entry. Bounded to one tick of staleness.
    """
    record = AgentRecord(name="claude-code-8421", pid=8421, command="claude")
    registry.register(record)
    monkeypatch.setattr("app.fleet_monitoring.sampler.AgentRegistry", lambda: registry)
    monkeypatch.setattr("app.fleet_monitoring.sampler.probe", lambda _pid: fake_snapshot)

    source = _FakeSource(chunks=["chunk-1"])
    meter = _FakeMeter({"chunk-1": TokenSample.from_tokens(100, model="claude-sonnet-4-5")})
    _stub_token_pipeline(monkeypatch, provider="claude-code", source=source, meter=meter)

    task = start_sampler(interval=0.01)
    await asyncio.sleep(0.03)

    assert get_tokens_per_min(8421) is not None

    # Forget the agent; the next tick's GC pass should clear its
    # tracker entry. Give the loop a few iterations to land.
    registry.forget(8421)
    await asyncio.sleep(0.05)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        _ = await task

    assert get_tokens_per_min(8421) is None


@pytest.mark.asyncio
async def test_provider_unknown_skips_token_path_without_crashing(
    monkeypatch: pytest.MonkeyPatch,
    registry: AgentRegistry,
    fake_snapshot: ProcessSnapshot,
) -> None:
    """An agent whose ``provider`` cannot be resolved must not crash
    the sampler — it just keeps the resource-snapshot path and
    leaves tokens/cost as ``-``.
    """
    registry.register(AgentRecord(name="my-custom-bot", pid=8421, command="bin"))
    monkeypatch.setattr("app.fleet_monitoring.sampler.AgentRegistry", lambda: registry)
    monkeypatch.setattr("app.fleet_monitoring.sampler.probe", lambda _pid: fake_snapshot)
    monkeypatch.setattr("app.fleet_monitoring.sampler.provider_for", lambda _record: None)

    task = start_sampler(interval=0.01)
    await asyncio.sleep(0.03)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        _ = await task

    # Snapshot path still works.
    assert get_snapshot(8421) == fake_snapshot
    # Token path was skipped — no tracker entry.
    assert get_tokens_per_min(8421) is None
    assert get_usd_per_hour(8421) is None


@pytest.mark.asyncio
async def test_schema_invalid_agents_yaml_does_not_crash_loop(
    monkeypatch: pytest.MonkeyPatch,
    registry: AgentRegistry,
    fake_snapshot: ProcessSnapshot,
) -> None:
    """A hand-edited ``agents.yaml`` with a typo'd field raises
    ``ValidationError``. The sampler must swallow it; the dashboard
    stays alive even when the user's config is unparseable.
    """
    from pydantic import ValidationError

    registry.register(AgentRecord(name="claude-code-8421", pid=8421, command="claude"))
    monkeypatch.setattr("app.fleet_monitoring.sampler.AgentRegistry", lambda: registry)
    monkeypatch.setattr("app.fleet_monitoring.sampler.probe", lambda _pid: fake_snapshot)

    def _raise() -> None:
        raise ValidationError.from_exception_data("AgentsConfig", [])

    monkeypatch.setattr("app.fleet_monitoring.sampler.load_agents_config", _raise)

    task = start_sampler(interval=0.01)
    await asyncio.sleep(0.03)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        _ = await task

    # Loop didn't tear down; snapshot path still works.
    assert get_snapshot(8421) == fake_snapshot
