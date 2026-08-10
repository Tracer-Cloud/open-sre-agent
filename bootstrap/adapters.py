"""Registration steps a process opts into, one function per step.

``surfaces`` and ``gateway`` may not import each other, so each used to keep a
byte-identical copy of this registration and synchronise it by comment. It lives
here once; :mod:`bootstrap.process` composes the steps a profile asks for.

Imports stay inside the functions: registration pulls in the tool and
integration registries, and a host that only needs adapters should not pay for
the scheduler ones.
"""

from __future__ import annotations


def install_investigation_api() -> None:
    """Wire :meth:`AgentSession.investigate` to the canonical payload runner.

    ``agent_harness`` must not import ``tools``; this composition-root step
    installs the callable the session API dispatches through.
    """
    from core.agent_harness.investigation_api import install_investigation_payload_runner
    from tools.investigation.capability import run_investigation_payload

    install_investigation_payload_runner(run_investigation_payload)


def install_harness_adapters() -> None:
    """Register the integration and tool adapters the harness resolves through.

    Without this a harness starts but no tool is available — the ports report
    nothing until both registries have been installed. Also installs the
    investigation payload runner used by :meth:`AgentSession.investigate`.
    """
    import platform.harness_ports as harness_ports
    from integrations.harness_adapters import (
        register_harness_adapters as register_integrations,
    )
    from tools.harness_adapters import register_harness_adapters as register_tools
    from tools.interactive_shell.subprocess_presenter import (
        headless_subprocess_presenter_factory,
    )

    register_integrations()
    register_tools()
    harness_ports.set_subprocess_presenter_factory(headless_subprocess_presenter_factory)
    # Shell / REPL / gateway slash surface: CTA must name a runnable command.
    harness_ports.set_integration_setup_command(
        lambda service_id: f"/integrations setup {service_id}"
    )
    install_investigation_api()


def install_scheduler_runners() -> None:
    """Register the runners scheduled tasks dispatch through.

    Investigation first: the scheduled-agent runner resolves against it.
    """
    from integrations.scheduled_agent_bootstrap import install as install_scheduled_agent
    from tools.investigation.scheduler_bootstrap import (
        install as install_investigation_runner,
    )

    install_investigation_runner()
    install_scheduled_agent()


__all__ = [
    "install_harness_adapters",
    "install_investigation_api",
    "install_scheduler_runners",
]
