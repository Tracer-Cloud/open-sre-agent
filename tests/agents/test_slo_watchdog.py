import asyncio
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import Mock

import pytest

from app.agents import AgentRecord, AgentRegistry
from app.agents.config import AgentBudget, AgentsConfig
from app.agents.probe import ProcessSnapshot
from app.agents.slo_watchdog import (
    _COOL_DOWN_INTERVAL_MINUTES,
    SLOBreach,
    _alert_trigger_registry,
    check_slo_breach,
    register_slo_alert,
    start_slo_watchdog,
    suppress_slo_alert,
)


@pytest.mark.parametrize(
    "budget, started_at, now, last_output, hourly_spend_usd, error_rate_pct, expectation",
    [
        # breach progress minutes
        (
            AgentBudget(hourly_budget_usd=1, progress_minutes=1, error_rate_pct=5),
            datetime(2026, 5, 14, 12, 0, 0, tzinfo=UTC),  # started_at
            datetime(2026, 5, 14, 12, 5, 0, tzinfo=UTC),  # now
            datetime(2026, 5, 14, 12, 2, 0, tzinfo=UTC),  # last_output_at
            0.5,
            2,
            SLOBreach(slo_type="progress_minutes", detail="3m no progress (threshold: 1m)"),
        ),
        # breach hourly budget
        (
            AgentBudget(hourly_budget_usd=1, progress_minutes=30, error_rate_pct=5),
            datetime(2026, 5, 14, 12, 0, 0, tzinfo=UTC),  # started_at
            datetime(2026, 5, 14, 12, 25, 0, tzinfo=UTC),  # now
            datetime(2026, 5, 14, 12, 15, 0, tzinfo=UTC),  # last_output_at
            3,
            2,
            SLOBreach(slo_type="hourly_budget_usd", detail="spent $3 (threshold: $1/h)"),
        ),
        # breach error rate
        (
            AgentBudget(hourly_budget_usd=5, progress_minutes=30, error_rate_pct=5),
            datetime(2026, 5, 14, 12, 0, 0, tzinfo=UTC),  # started_at
            datetime(2026, 5, 14, 12, 20, 0, tzinfo=UTC),  # now
            datetime(2026, 5, 14, 12, 15, 0, tzinfo=UTC),  # last_output_at
            3.5,
            10,
            SLOBreach(slo_type="error_rate_pct", detail="10.0% error rate (threshold: 5.0%)"),
        ),
        # multiple breaches: progress and hourly budget
        (
            AgentBudget(hourly_budget_usd=1, progress_minutes=1, error_rate_pct=5),
            datetime(2026, 5, 14, 12, 0, 0, tzinfo=UTC),  # started_at
            datetime(2026, 5, 14, 12, 20, 0, tzinfo=UTC),  # now
            datetime(2026, 5, 14, 12, 18, 0, tzinfo=UTC),  # last_output_at
            2.5,
            20,
            SLOBreach(slo_type="progress_minutes", detail="2m no progress (threshold: 1m)"),
        ),
        # multiple breaches: hourly budget and error rate
        (
            AgentBudget(hourly_budget_usd=1, progress_minutes=10, error_rate_pct=5),
            datetime(2026, 5, 14, 12, 0, 0, tzinfo=UTC),  # started_at
            datetime(2026, 5, 14, 12, 20, 0, tzinfo=UTC),  # now
            datetime(2026, 5, 14, 12, 18, 0, tzinfo=UTC),  # last_output_at
            2.5,
            20,
            SLOBreach(slo_type="hourly_budget_usd", detail="spent $2.5 (threshold: $1/h)"),
        ),
        # last_output_at is not provided (None)
        (
            AgentBudget(hourly_budget_usd=1, progress_minutes=5, error_rate_pct=5),
            datetime(2026, 5, 14, 12, 0, 0, tzinfo=UTC),  # started_at
            datetime(2026, 5, 14, 12, 7, 0, tzinfo=UTC),  # now
            None,  # last_output_at
            0.5,
            2,
            SLOBreach(slo_type="progress_minutes", detail="7m no progress (threshold: 5m)"),
        ),
        # no breach
        (
            AgentBudget(hourly_budget_usd=5, progress_minutes=30, error_rate_pct=5),
            datetime(2026, 5, 14, 12, 0, 0, tzinfo=UTC),  # started_at
            datetime(2026, 5, 14, 12, 29, 0, tzinfo=UTC),  # now
            datetime(2026, 5, 14, 12, 15, 0, tzinfo=UTC),  # last_output_at
            4,
            2,
            None,  # expectation
        ),
        # hourly_spend_usd is not provided (None)
        (
            AgentBudget(hourly_budget_usd=1, progress_minutes=30, error_rate_pct=5),
            datetime(2026, 5, 14, 12, 0, 0, tzinfo=UTC),  # started_at
            datetime(2026, 5, 14, 12, 25, 0, tzinfo=UTC),  # now
            datetime(2026, 5, 14, 12, 15, 0, tzinfo=UTC),  # last_output_at
            None,  # hourly_spend_usd
            2,
            None,  # expectation
        ),
        # error_rate_pct is not provided (None)
        (
            AgentBudget(hourly_budget_usd=1, progress_minutes=30, error_rate_pct=5),
            datetime(2026, 5, 14, 12, 0, 0, tzinfo=UTC),  # started_at
            datetime(2026, 5, 14, 12, 25, 0, tzinfo=UTC),  # now
            datetime(2026, 5, 14, 12, 15, 0, tzinfo=UTC),  # last_output_at
            0.5,
            None,  # error_rate_pct
            None,  # expectation
        ),
        # edge case: now == last_output_at (zero gap)
        (
            AgentBudget(hourly_budget_usd=1, progress_minutes=1, error_rate_pct=5),
            datetime(2026, 5, 14, 12, 0, 0, tzinfo=UTC),  # started_at
            datetime(2026, 5, 14, 12, 5, 0, tzinfo=UTC),  # now
            datetime(2026, 5, 14, 12, 5, 0, tzinfo=UTC),  # last_output_at
            0.9,
            2,
            None,
        ),
        # edge case: now < last_output_at (clock skew)
        (
            AgentBudget(hourly_budget_usd=5, progress_minutes=1, error_rate_pct=5),
            datetime(2026, 5, 14, 12, 0, 0, tzinfo=UTC),  # started_at
            datetime(2026, 5, 14, 12, 3, 0, tzinfo=UTC),  # now
            datetime(2026, 5, 14, 12, 5, 0, tzinfo=UTC),  # last_output_at
            4,
            2,
            None,
        ),
        # edge case: None agent budget field (progress_minutes is None)
        (
            AgentBudget(hourly_budget_usd=1, progress_minutes=None, error_rate_pct=5),
            datetime(2026, 5, 14, 12, 0, 0, tzinfo=UTC),  # started_at
            datetime(2026, 5, 14, 12, 5, 0, tzinfo=UTC),  # now
            datetime(2026, 5, 14, 12, 2, 0, tzinfo=UTC),  # last_output_at
            0.5,
            2,
            None,
        ),
        # edge case: (gap == threshold)
        (
            AgentBudget(hourly_budget_usd=1, progress_minutes=1, error_rate_pct=5),
            datetime(2026, 5, 14, 12, 0, 0, tzinfo=UTC),  # started_at
            datetime(2026, 5, 14, 12, 3, 0, tzinfo=UTC),  # now
            datetime(2026, 5, 14, 12, 2, 0, tzinfo=UTC),  # last_output_at
            0.5,
            2,
            SLOBreach(slo_type="progress_minutes", detail="1m no progress (threshold: 1m)"),
        ),
    ],
)
def test_check_slo_breach(
    budget,
    started_at,
    now,
    last_output,
    hourly_spend_usd,
    error_rate_pct,
    expectation,
) -> None:
    """Table-driven breach detection covering each SLO type in isolation,
    priority when multiple breach simultaneously, None observed values
    (stub collectors), None thresholds (unconfigured SLOs), and boundary
    edge cases (zero gap, clock skew, exact-threshold).
    """
    assert (
        check_slo_breach(
            budget,
            started_at=started_at,
            now=now,
            last_output_at=last_output,
            hourly_spend_usd=hourly_spend_usd,
            error_rate_pct=error_rate_pct,
        )
        == expectation
    )


class TestSuppressSloAlert:
    @pytest.fixture(autouse=True)
    def clean_up_alert_trigger_registry(self) -> None:
        _alert_trigger_registry.clear()

    @pytest.mark.parametrize(
        "key, alert_at, now, expectation",
        [
            # below cool down interval and key exits, suppress alert (true)
            (
                "cursor.progress_minutes",
                datetime(2026, 5, 14, 12, 2, 0, tzinfo=UTC),  # alert_at
                datetime(2026, 5, 14, 12, 25, 0, tzinfo=UTC),  # now
                True,
            ),
            # above cool down interval and key exists, suppress alert (false)
            (
                "cursor.hourly_budget_usd",
                datetime(2026, 5, 14, 11, 0, 0, tzinfo=UTC),  # alert_at
                datetime(2026, 5, 14, 12, 31, 0, tzinfo=UTC),  # now
                False,
            ),
            # key is empty, suppress alert (true)
            (
                "",  # empty key will not be registered
                datetime(2026, 5, 14, 12, 0, 0, tzinfo=UTC),  # alert_at
                datetime(2026, 5, 14, 12, 30, 0, tzinfo=UTC),  # now
                True,
            ),
            # edge case: at the boundary, suppress alert (false)
            (
                "cursor.error_rate_pct",
                datetime(2026, 5, 14, 12, 2, 0, tzinfo=UTC),  # alert_at
                datetime(2026, 5, 14, 12, 32, 0, tzinfo=UTC),  # now
                False,
            ),
        ],
    )
    def test_suppress_slo_alert(self, key, alert_at, now, expectation) -> None:
        register_slo_alert(key, alert_at)
        assert suppress_slo_alert(key, now) == expectation

    def test_first_breach_not_suppressed(self) -> None:
        assert (
            suppress_slo_alert(
                "claude.hourly_budget_usd", datetime(2026, 5, 19, 19, 0, 0, tzinfo=UTC)
            )
            is not True
        )


@pytest.fixture
def registry(tmp_path: Path) -> AgentRegistry:
    return AgentRegistry(path=tmp_path / "agents.jsonl")


@pytest.fixture
def config() -> AgentsConfig:
    return AgentsConfig()


@pytest.fixture
def fake_snapshot() -> ProcessSnapshot:
    return ProcessSnapshot(
        pid=8421,
        cpu_percent=23.5,
        rss_mb=128.0,
        num_fds=42,
        num_connections=3,
        status="running",
        started_at=datetime(2026, 5, 21, 12, 0, 0, tzinfo=UTC),
    )


@pytest.fixture(autouse=True)
def clean_up_alert_trigger_registry() -> None:
    _alert_trigger_registry.clear()


@pytest.mark.asyncio
async def test_slo_watchdog_invokes_callback_on_breach(
    monkeypatch: pytest.MonkeyPatch,
    config: AgentsConfig,
    registry: AgentRegistry,
    fake_snapshot: ProcessSnapshot,
) -> None:
    config.agents = {
        "claude": AgentBudget(hourly_budget_usd=5, progress_minutes=2, error_rate_pct=3)
    }
    registry.register(
        AgentRecord(
            name="claude",
            pid=8421,
            command="claude --dangerously-skip-permissions",
            registered_at="2026-05-07T12:00:00+00:00",
        )
    )
    now = datetime(2026, 5, 21, 12, 5, 0, tzinfo=UTC)
    on_breach = Mock()

    monkeypatch.setattr("app.agents.slo_watchdog.load_agents_config", lambda: config)
    monkeypatch.setattr("app.agents.slo_watchdog.AgentRegistry", lambda: registry)
    monkeypatch.setattr("app.agents.slo_watchdog.get_snapshot", lambda _pid: fake_snapshot)
    monkeypatch.setattr("app.agents.slo_watchdog.get_usd_per_hour", lambda _pid: 2.0)
    monkeypatch.setattr("app.agents.slo_watchdog._get_now", lambda _tz: now)

    task = start_slo_watchdog(on_breach=on_breach, interval=0.01)
    await asyncio.sleep(0.05)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        _ = await task

    on_breach.assert_called_once_with(
        "claude", SLOBreach(slo_type="progress_minutes", detail="5m no progress (threshold: 2m)")
    )
    assert _alert_trigger_registry["claude.progress_minutes"] == now + _COOL_DOWN_INTERVAL_MINUTES


@pytest.mark.asyncio
async def test_slo_watchdog_does_not_invoke_callback_on_no_snapshot(
    monkeypatch: pytest.MonkeyPatch,
    config: AgentsConfig,
    registry: AgentRegistry,
) -> None:
    config.agents = {
        "cursor": AgentBudget(hourly_budget_usd=1, progress_minutes=2, error_rate_pct=3)
    }
    registry.register(
        AgentRecord(
            name="cursor",
            pid=8421,
            command="cursor",
            registered_at="2026-05-09T12:00:00+00:00",
        )
    )
    now = datetime(2026, 5, 21, 11, 0, 0, tzinfo=UTC)
    on_breach = Mock()

    monkeypatch.setattr("app.agents.slo_watchdog.load_agents_config", lambda: config)
    monkeypatch.setattr("app.agents.slo_watchdog.AgentRegistry", lambda: registry)
    monkeypatch.setattr("app.agents.slo_watchdog.get_snapshot", lambda _pid: None)
    monkeypatch.setattr("app.agents.slo_watchdog._get_now", lambda _tz: now)

    task = start_slo_watchdog(on_breach=on_breach, interval=0.01)
    await asyncio.sleep(0.05)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        _ = await task

    on_breach.assert_not_called()


@pytest.mark.asyncio
async def test_slo_watchdog_does_not_invoke_callback_on_no_budget(
    monkeypatch: pytest.MonkeyPatch,
    config: AgentsConfig,
    registry: AgentRegistry,
    fake_snapshot: ProcessSnapshot,
) -> None:
    config.agents = {
        "cursor": AgentBudget(hourly_budget_usd=1, progress_minutes=2, error_rate_pct=3)
    }
    registry.register(
        AgentRecord(
            name="codex",
            pid=8421,
            command="codex",
            registered_at="2026-05-09T12:00:00+00:00",
        )
    )
    now = datetime(2026, 5, 23, 11, 0, 0, tzinfo=UTC)
    on_breach = Mock()

    monkeypatch.setattr("app.agents.slo_watchdog.load_agents_config", lambda: config)
    monkeypatch.setattr("app.agents.slo_watchdog.AgentRegistry", lambda: registry)
    monkeypatch.setattr("app.agents.slo_watchdog.get_snapshot", lambda _pid: fake_snapshot)
    monkeypatch.setattr("app.agents.slo_watchdog._get_now", lambda _tz: now)

    task = start_slo_watchdog(on_breach=on_breach, interval=0.01)
    await asyncio.sleep(0.05)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        _ = await task

    on_breach.assert_not_called()


@pytest.mark.asyncio
async def test_slo_watchdog_does_not_crash_on_empty_registry(
    monkeypatch: pytest.MonkeyPatch,
    config: AgentsConfig,
    registry: AgentRegistry,
) -> None:
    now = datetime(2026, 5, 22, 11, 0, 0, tzinfo=UTC)
    on_breach = Mock()

    monkeypatch.setattr("app.agents.slo_watchdog.load_agents_config", lambda: config)
    monkeypatch.setattr("app.agents.slo_watchdog.AgentRegistry", lambda: registry)
    monkeypatch.setattr("app.agents.slo_watchdog._get_now", lambda _tz: now)

    task = start_slo_watchdog(on_breach=on_breach, interval=0.01)
    await asyncio.sleep(0.05)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        _ = await task

    on_breach.assert_not_called()


@pytest.mark.asyncio
async def test_slo_watchdog_cooldown_suppression(
    monkeypatch: pytest.MonkeyPatch,
    config: AgentsConfig,
    registry: AgentRegistry,
    fake_snapshot: ProcessSnapshot,
) -> None:
    config.agents = {
        "codex": AgentBudget(hourly_budget_usd=7, progress_minutes=2, error_rate_pct=3)
    }
    registry.register(
        AgentRecord(
            name="codex",
            pid=8421,
            command="codex",
            registered_at="2026-05-07T12:00:00+00:00",
        )
    )
    now = datetime(2026, 5, 23, 12, 15, 0, tzinfo=UTC)
    alert_at = datetime(2026, 5, 23, 12, 5, 0, tzinfo=UTC)
    register_slo_alert("codex.progress_minutes", alert_at)
    on_breach = Mock()

    monkeypatch.setattr("app.agents.slo_watchdog.load_agents_config", lambda: config)
    monkeypatch.setattr("app.agents.slo_watchdog.AgentRegistry", lambda: registry)
    monkeypatch.setattr("app.agents.slo_watchdog.get_snapshot", lambda _pid: fake_snapshot)
    monkeypatch.setattr("app.agents.slo_watchdog.get_usd_per_hour", lambda _pid: 2.0)
    monkeypatch.setattr("app.agents.slo_watchdog._get_now", lambda _tz: now)

    task = start_slo_watchdog(on_breach=on_breach, interval=0.01)
    await asyncio.sleep(0.05)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        _ = await task

    on_breach.assert_not_called()
    assert (
        _alert_trigger_registry["codex.progress_minutes"] == alert_at + _COOL_DOWN_INTERVAL_MINUTES
    )
