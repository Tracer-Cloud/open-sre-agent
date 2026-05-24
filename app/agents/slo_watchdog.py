from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta, tzinfo

from app.agents import AgentRegistry
from app.agents.config import AgentBudget, load_agents_config
from app.agents.sampler import get_snapshot, get_usd_per_hour

logger = logging.getLogger(__name__)

# Minimum time between repeated alerts for the same agent + SLO pair.
# After a breach fires, subsequent breaches for the same key are
# suppressed until this interval elapses.
_COOL_DOWN_INTERVAL_MINUTES: timedelta = timedelta(minutes=30)

# Maps ``"{agent_name}.{slo_type}"`` → earliest datetime at which
# that key is eligible to fire again. Populated by ``register_slo_alert``
# after a breach is dispatched; consulted by ``suppress_slo_alert``
# before dispatching.
_alert_trigger_registry: dict[str, datetime] = {}


@dataclass(frozen=True)
class SLOBreach:
    """Immutable result of a threshold violation.

    ``slo_type`` matches the ``AgentBudget`` field name that was breached.
    ``detail`` is a human-readable one-liner suitable for REPL banners and
    alert payloads.
    """

    slo_type: str
    detail: str


def register_slo_alert(key: str, alert_at: datetime) -> None:
    """Record that ``key`` fired at ``alert_at``, suppressing re-fire
    until ``alert_at + _COOL_DOWN_INTERVAL_MINUTES``.
    Called by the watchdog loop immediately after dispatching an
    investigation for a breach.  Empty keys are silently ignored
    (defensive guard against malformed agent names).
    """
    if key != "":
        _alert_trigger_registry[key] = alert_at + _COOL_DOWN_INTERVAL_MINUTES


def suppress_slo_alert(key: str, now: datetime) -> bool:
    """Return True if this breach should be suppressed (still in cooldown).
    ``key`` is ``"{agent_name}.{slo_type}"`` — the same string passed
    to ``register_slo_alert`` after a previous dispatch.
    Returns False (= fire the alert) when:
      - The key has never been registered (first breach).
      - The cooldown period has elapsed since the last dispatch.
    Returns True (= suppress) when:
      - The key is empty (invalid, defensive guard).
      - The key was registered and cooldown has not yet expired.
    """

    # Empty key indicates a programming error upstream; suppress rather
    # than dispatching an investigation with no agent identity.
    if key == "":
        return True

    trigger_time = _alert_trigger_registry.get(key)
    if trigger_time is not None:
        return now < trigger_time  # True when now < last trigger_time

    # Key not in registry — first-ever breach for this agent+SLO. Allow it.
    return False


def check_slo_breach(
    budget: AgentBudget,
    *,
    started_at: datetime,
    now: datetime,
    last_output_at: datetime | None,
    hourly_spend_usd: float | None,
    error_rate_pct: float | None,
) -> SLOBreach | None:
    """Return the highest-priority SLO breach for a single agent, or None.

    Checks are evaluated in priority order — progress > cost > error —
    and the first breach wins.  When multiple SLOs are violated
    simultaneously, only the most actionable one fires; remaining
    breaches surface on subsequent watchdog cycles after cooldown.

    ``error_rate_pct`` is an optional observed value from a collector
    that do not yet exist; passing ``None`` skips that check.
    Similarly, a ``None`` threshold in ``budget``
    means the user has not configured that SLO — the check is skipped
    regardless of the observed value.
    """
    if now.tzinfo is None:
        raise ValueError("now must be timezone-aware (e.g. datetime.now(UTC)), got naive datetime")

    if started_at.tzinfo is None:
        raise ValueError(
            "started_at must be timezone-aware (e.g. datetime.now(UTC)), got naive datetime"
        )

    # last_output_at is optional, when not provided (collector does not exist), we fall back to using
    # the process started_at datetime.
    if last_output_at is None:
        last_output_at = started_at
    elif last_output_at.tzinfo is None:
        raise ValueError(
            "last_output_at must be timezone-aware (e.g. datetime.now(UTC)), got naive datetime"
        )

    # Clock skew / negative delta: if now < last_output_at, total_seconds()
    # is negative and floor-division yields a negative minute count, which
    # can never >= a non-negative threshold — no false-positive breach.
    no_progress = (now - last_output_at).total_seconds() // 60

    # Return the first breach found (priority order: progress > cost > error)
    if budget.progress_minutes is not None and no_progress >= budget.progress_minutes:
        return SLOBreach(
            slo_type="progress_minutes",
            detail=f"{no_progress:.0f}m no progress (threshold: {budget.progress_minutes}m)",
        )
    elif (
        hourly_spend_usd is not None
        and budget.hourly_budget_usd is not None
        and hourly_spend_usd >= budget.hourly_budget_usd
    ):
        return SLOBreach(
            slo_type="hourly_budget_usd",
            detail=f"spent ${hourly_spend_usd:g} (threshold: ${budget.hourly_budget_usd:g}/h)",
        )
    elif (
        error_rate_pct is not None
        and budget.error_rate_pct is not None
        and error_rate_pct >= budget.error_rate_pct
    ):
        return SLOBreach(
            slo_type="error_rate_pct",
            detail=f"{error_rate_pct:.1f}% error rate (threshold: {budget.error_rate_pct:.1f}%)",
        )

    return None


def _get_now(tz: tzinfo = UTC) -> datetime:
    """Return the current time. Indirected so tests can monkeypatch the clock."""
    return datetime.now(tz=tz)


async def _slo_watchdog_loop(
    on_breach: Callable[[str, SLOBreach], None], interval: float = 30.0
) -> None:
    """Check every registered agent against its configured SLOs each tick.
    Runs until cancelled. Per-agent failures are logged at debug level
    and never interrupt the loop — a single misconfigured or dead agent
    does not tear down the watchdog.
    On breach (not suppressed by cooldown), ``on_breach(agent_name, breach)``
    is invoked synchronously. The caller is responsible for rendering the
    banner and eventually dispatching an investigation.
    """

    while True:
        try:
            budgets = load_agents_config().agents
        except Exception:
            logger.warning("slo watchdog: failed to load agents config", exc_info=True)
            await asyncio.sleep(interval)
            continue
        try:
            registry = AgentRegistry()
            agents = registry.list()
        except Exception:
            logger.warning("slo watchdog: failed to load agent registry", exc_info=True)
            await asyncio.sleep(interval)
            continue
        now = _get_now(UTC)
        for agent in agents:
            try:
                snapshot = get_snapshot(agent.pid)
                if snapshot is None:
                    continue

                budget = budgets.get(agent.name)
                if budget is None:
                    continue

                # Stub: collectors for error_rate_pct, and
                # last_output_at do not exist yet — all passed as None.
                breach = check_slo_breach(
                    budget,
                    started_at=snapshot.started_at,
                    now=now,
                    last_output_at=None,
                    hourly_spend_usd=get_usd_per_hour(agent.pid),
                    error_rate_pct=None,
                )
                if breach is None:
                    continue

                key = f"{agent.name}.{breach.slo_type}"
                if not suppress_slo_alert(key, now):
                    register_slo_alert(key, now)
                    on_breach(agent.name, breach)
            except Exception:
                logger.debug(
                    "slo check failed for agent %s with pid %d",
                    agent.name,
                    agent.pid,
                    exc_info=True,
                )
        await asyncio.sleep(interval)


def start_slo_watchdog(
    on_breach: Callable[[str, SLOBreach], None], interval: float = 30.0
) -> asyncio.Task[None]:
    """Launch the background SLO watchdog and return the cancellable task."""
    return asyncio.create_task(_slo_watchdog_loop(on_breach, interval))
