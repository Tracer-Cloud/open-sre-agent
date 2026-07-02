"""Investigation-runner seam for the scheduled-delivery subsystem.

The scheduler needs to invoke the investigation pipeline (``run_investigation``
in :mod:`tools.investigation.capability`) to build reports for kinds such as
``daily_summary`` and ``weekly_audit``. Doing that directly from
``platform.scheduler`` reintroduces a ``platform -> tools`` layering violation
(T-4 layering audit, issue #3352).

This module inverts the dependency: the scheduler declares a small
:class:`InvestigationRunner` protocol and calls it through
:func:`invoke_investigation_runner`. A startup path in a higher layer (the
``opensre cron`` command and the scheduler bootstrap in
:mod:`tools.investigation.scheduler_bootstrap`) registers the concrete
implementation via :func:`register_investigation_runner`.

Tests that patch ``tools.investigation.capability.run_investigation`` continue
to work because the bootstrap module re-reads that attribute on every scheduler
invocation instead of binding it at import time.
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


class InvestigationRunnerNotRegisteredError(RuntimeError):
    """Raised when the scheduler executes a task before a runner is registered."""


_runner: InvestigationRunner | None = None


def register_investigation_runner(runner: InvestigationRunner | None) -> None:
    """Bind (or clear) the concrete investigation runner used by the scheduler.

    Called from the layer that may legally depend on both ``platform`` and
    ``tools`` (the CLI ``cron`` command and the scheduler bootstrap). Passing
    ``None`` clears the binding — useful in tests.
    """
    global _runner
    _runner = runner


def get_investigation_runner() -> InvestigationRunner | None:
    """Return the currently registered runner, if any."""
    return _runner


def invoke_investigation_runner(alert_payload: AlertPayload) -> InvestigationResult | None:
    """Invoke the currently registered investigation runner.

    Raises :class:`InvestigationRunnerNotRegisteredError` when no runner has
    been registered. This is a hard failure to ensure the scheduler never
    silently no-ops when the wiring is missing.
    """
    if _runner is None:
        raise InvestigationRunnerNotRegisteredError(
            "Scheduler has no investigation runner registered. Call "
            "tools.investigation.scheduler_bootstrap.install() at startup "
            "(the `opensre cron` command does this automatically)."
        )
    return _runner(alert_payload)


__all__ = [
    "AlertPayload",
    "InvestigationResult",
    "InvestigationRunner",
    "InvestigationRunnerNotRegisteredError",
    "get_investigation_runner",
    "invoke_investigation_runner",
    "register_investigation_runner",
]
