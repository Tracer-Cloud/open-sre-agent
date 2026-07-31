"""Register harness adapters and scheduler runners for a gateway process.

Composition root: ``tools`` and ``integrations`` are sibling layers and must
not import each other (``.importlinter.strict``). Gateway must not import
``surfaces`` either, so this module mirrors
:mod:`surfaces.shared.runtime_bootstrap` — keep the flag matrix and call
order in sync.

Typical flags:

- Gateway boot: adapters early (``scheduler_runners=False``), runners when the
  scheduler stage starts (``harness_adapters=False``).
- Standalone webapp: adapters only.
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
