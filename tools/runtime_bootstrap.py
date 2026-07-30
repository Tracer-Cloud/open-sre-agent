"""Shared process bootstrap for harness ports and scheduler runners.

Surfaces (gateway, CLI cron, sentry digest, webapp) used to paste the same
four registration calls. One installer keeps that set DRY without pulling
``integrations/`` / ``tools/`` into ``core/agent_harness`` (forbidden).

Call :func:`install_runtime` from each composition root; pass flags when a
surface only needs adapters (webapp) or only runners (gateway scheduler stage).
"""

from __future__ import annotations


def install_runtime(
    *,
    harness_adapters: bool = True,
    scheduler_runners: bool = True,
) -> None:
    """Register harness adapters and/or scheduler runners for this process.

    Idempotent at the adapter/runner level (re-registering the same callables).
    """
    if harness_adapters:
        from integrations.harness_adapters import (
            register_harness_adapters as register_integrations,
        )
        from tools.harness_adapters import register_harness_adapters as register_tools

        register_integrations()
        register_tools()
    if scheduler_runners:
        from integrations.scheduled_agent_bootstrap import install as install_scheduled_agent
        from tools.investigation.scheduler_bootstrap import (
            install as install_investigation_runner,
        )

        install_investigation_runner()
        install_scheduled_agent()


__all__ = ["install_runtime"]
