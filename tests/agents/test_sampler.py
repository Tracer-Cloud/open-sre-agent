from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from pathlib import Path

import pytest

from app.agents.probe import ProcessSnapshot
from app.agents.registry import AgentRecord, AgentRegistry
from app.agents.sampler import _latest, get_snapshot, start_sampler


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
def _clear_sampler_state() -> None:
    """Reset module-level state between tests."""
    _latest.clear()


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
    monkeypatch.setattr("app.agents.sampler.AgentRegistry", lambda: registry)

    monkeypatch.setattr("app.agents.sampler.probe", lambda _pid: fake_snapshot)

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
    monkeypatch.setattr("app.agents.sampler.AgentRegistry", lambda: registry)

    monkeypatch.setattr("app.agents.sampler.probe", lambda _pid: None)

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
    monkeypatch.setattr("app.agents.sampler.AgentRegistry", lambda: registry)

    def mock_probe(pid: int) -> ProcessSnapshot | None:
        if pid == 1111:
            raise RuntimeError("simulated psutil failure")
        return fake_snapshot

    monkeypatch.setattr("app.agents.sampler.probe", mock_probe)

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
    monkeypatch.setattr("app.agents.sampler.AgentRegistry", lambda: registry)

    monkeypatch.setattr("app.agents.sampler.probe", lambda _pid: None)

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
    monkeypatch.setattr("app.agents.sampler.AgentRegistry", lambda: registry)

    monkeypatch.setattr("app.agents.sampler.probe", lambda _pid: None)

    task = start_sampler(interval=0.01)
    await asyncio.sleep(0.02)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        _ = await task

    assert get_snapshot(8421) is None
