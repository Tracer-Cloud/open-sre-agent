"""Investigation-runner seam for the scheduled-delivery subsystem.

The scheduler needs to invoke the investigation pipeline (``run_investigation``
in :mod:`tools.investigation.capability`) to build reports for kinds such as
``daily_summary`` and ``weekly_audit``. Doing that directly from
``infrastructure.scheduling.scheduler`` reintroduces a ``platform -> tools`` layering violation
(T-4 layering audit, issue #3352).

This module inverts the dependency: the scheduler declares a small
:class:`InvestigationRunner` protocol, and the composition root builds the
concrete implementation and passes it in as part of
:class:`~infrastructure.scheduling.scheduler.runners.SchedulerRunners`. This
module only declares the contract.
"""

from __future__ import annotations

from typing import Any, Protocol

AlertPayload = dict[str, Any]
InvestigationResult = dict[str, Any]


class InvestigationRunner(Protocol):
    """Callable that consumes an alert payload and returns an investigation result.

    The scheduler treats a missing report as "quiet period" and never raises on
    empty results. Implementations should raise for genuine pipeline failures
    so the executor records ``FAILED`` in the run log.
    """

    def __call__(self, alert_payload: AlertPayload) -> InvestigationResult | None:
        """Run the investigation pipeline for ``alert_payload``."""


__all__ = ["AlertPayload", "InvestigationResult", "InvestigationRunner"]
