"""Build the investigation pipeline runner the scheduler dispatches through.

The scheduled-delivery subsystem in :mod:`infrastructure.scheduling.scheduler`
invokes an
:class:`infrastructure.scheduling.scheduler.investigation_runner.InvestigationRunner`
to build reports. ``infrastructure`` sits below ``tools`` in the layering
contract, so the runner is defined on this side of the boundary (T-4 layering
audit, issue #3352). The composition root
(:func:`bootstrap.adapters.scheduler_runners`) imports
:func:`run_scheduled_investigation` and hands it to the scheduler as part of the
``SchedulerRunners`` bundle.

The callable resolves ``run_investigation`` through the
:mod:`tools.investigation.capability` module attribute on every invocation, so
tests that monkeypatch that attribute continue to affect scheduler behavior
without any additional plumbing.
"""

from __future__ import annotations

from typing import cast

from infrastructure.scheduling.scheduler.investigation_runner import (
    AlertPayload,
    InvestigationResult,
)


def run_scheduled_investigation(alert_payload: AlertPayload) -> InvestigationResult | None:
    from tools.investigation import capability

    # ``run_investigation`` returns an ``AgentState`` TypedDict (dict-backed
    # at runtime). The scheduler contract is ``dict[str, Any] | None`` — the
    # ``AgentState`` value is a compatible dict, so we cast at the boundary
    # to keep the platform runner protocol vendor/framework-neutral.
    return cast(InvestigationResult | None, capability.run_investigation(alert_payload))


__all__ = ["run_scheduled_investigation"]
