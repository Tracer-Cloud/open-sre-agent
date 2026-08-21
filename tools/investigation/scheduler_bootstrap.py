"""Register the investigation pipeline as the scheduler's runner.

The scheduled-delivery subsystem in :mod:`infrastructure.scheduling.scheduler` invokes an
:class:`infrastructure.scheduling.scheduler.investigation_runner.InvestigationRunner` to build
reports. ``infrastructure`` sits below ``tools`` in the layering contract, so the
runner is registered from this side of the boundary (T-4 layering audit, issue
#3352). Call :func:`install` from any higher-layer entrypoint that expects the
scheduler to run investigations (e.g. ``opensre cron start`` / ``opensre cron
run``).

The registered callable resolves ``run_investigation`` through the
:mod:`tools.investigation.capability` module attribute on every invocation, so
tests that monkeypatch that attribute continue to affect scheduler behavior
without any additional plumbing.
"""

from __future__ import annotations

from typing import cast

from infrastructure.scheduling.scheduler.investigation_runner import (
    AlertPayload,
    InvestigationResult,
    register_investigation_runner,
)


def run_scheduled_investigation(alert_payload: AlertPayload) -> InvestigationResult | None:
    from tools.investigation import capability

    # ``run_investigation`` returns an ``AgentState`` TypedDict (dict-backed
    # at runtime). The scheduler contract is ``dict[str, Any] | None`` — the
    # ``AgentState`` value is a compatible dict, so we cast at the boundary
    # to keep the platform runner protocol vendor/framework-neutral.
    return cast(InvestigationResult | None, capability.run_investigation(alert_payload))


def install() -> None:
    """Bind the canonical investigation pipeline as the scheduler runner.

    Registers this seam alone. A host that wants both seams — and its capacity
    gate applied to them — builds ``SchedulerRunners`` through
    :func:`bootstrap.adapters.scheduler_runners` instead.

    Idempotent — re-registering the same shim is a no-op from the scheduler's
    perspective. Tests that need to swap the runner should call
    :func:`infrastructure.scheduling.scheduler.investigation_runner.register_investigation_runner`
    directly (or clear it with ``None``).
    """
    register_investigation_runner(run_scheduled_investigation)


__all__ = ["install", "run_scheduled_investigation"]
