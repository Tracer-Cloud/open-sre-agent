"""Register harness adapters and scheduler runners for a process.

Call sites (gateway, CLI cron, sentry digest, webapp) share this helper so the
registration set stays in one place. Lives under ``tools/`` because
``core/agent_harness`` must not import ``integrations/`` or ``tools/``.
"""

from __future__ import annotations


def install_runtime(
    *,
    harness_adapters: bool = True,
    scheduler_runners: bool = True,
) -> None:
    """Install adapters and/or scheduler runners. Safe to call more than once."""
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
