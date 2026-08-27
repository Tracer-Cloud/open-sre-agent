"""Registration steps a process opts into, one function per step.

``surfaces`` and ``gateway`` may not import each other, so each used to keep a
byte-identical copy of this registration and synchronise it by comment. It lives
here once; :mod:`bootstrap.process` composes the steps a profile asks for.

Imports stay inside the functions: registration pulls in the tool and
integration registries, and a host that only needs adapters should not pay for
the scheduler ones.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from infrastructure.scheduling.scheduler.delivery_bundle import ScheduledDeliveryAdapters
    from infrastructure.scheduling.scheduler.runners import SchedulerRunners


def install_harness_adapters() -> None:
    """Register the integration and tool adapters the harness resolves through.

    Without this a harness starts but no tool is available — the ports report
    nothing until both registries have been installed.
    """
    import infrastructure.harness_providers as harness_providers
    from integrations.harness_adapters import (
        register_harness_adapters as register_integrations,
    )
    from tools.harness_adapters import register_harness_adapters as register_tools
    from tools.interactive_shell.subprocess_presenter import (
        headless_subprocess_presenter_factory,
    )

    register_integrations()
    register_tools()
    harness_providers.SubprocessPresenterProvider(headless_subprocess_presenter_factory).install()
    # Shell / REPL / gateway slash surface: CTA must name a runnable command.
    harness_providers.IntegrationSetupCommand(
        lambda service_id: f"/integrations setup {service_id}"
    ).install()


def install_notification_adapters() -> tuple[str, ...]:
    """Register the outbound channels background-RCA notices dispatch through.

    Without this the registry is empty and every configured channel reports
    ``"unsupported"``, so the returned names are the diagnostic that tells an
    empty registry apart from a genuinely unknown channel.

    Called on background-RCA completion and when ``/background notify set``
    validates a channel, not at boot. Importing an adapter module does not pull
    its vendor transport, because each keeps its client import inside the
    delivery function.

    The adapter modules only define their adapter; this step is the one place
    that registers one. Nothing registers at import time, so a module imported
    on its own (a tool reaching for ``deliver_email_notification``, say) leaves
    the registry untouched, and this step still repopulates a registry a test
    cleared, because it registers objects rather than replaying a cached import.
    """
    from infrastructure.delivery.notifications.outbound_registry import (
        register_outbound_adapter,
        registered_outbound_adapter_names,
    )
    from integrations.buzz.background_adapter import buzz_background_adapter
    from integrations.rocketchat.background_adapter import rocketchat_background_adapter
    from integrations.smtp.background_adapter import email_background_adapter
    from integrations.telegram.background_adapter import telegram_background_adapter

    for adapter in (
        buzz_background_adapter,
        rocketchat_background_adapter,
        email_background_adapter,
        telegram_background_adapter,
    ):
        register_outbound_adapter(adapter)
    return registered_outbound_adapter_names()


def scheduler_runners() -> SchedulerRunners:
    """Assemble the runners scheduled tasks dispatch through.

    The only layer that may see both ``integrations`` and ``tools``, so the
    bundle is built here and handed to whichever host installs it.
    """
    from infrastructure.scheduling.scheduler.runners import SchedulerRunners
    from integrations.scheduled_agent_bootstrap import run_scheduled_agent_digest
    from tools.investigation.scheduler_bootstrap import run_scheduled_investigation

    return SchedulerRunners(
        agent=run_scheduled_agent_digest,
        investigation=run_scheduled_investigation,
    )


def scheduled_delivery_adapters() -> ScheduledDeliveryAdapters:
    """Assemble the per-provider adapters scheduled delivery dispatches through.

    The only layer that may see both ``integrations`` and ``infrastructure``, so
    the vendor adapters are bundled here and handed to whichever host installs it.
    """
    from infrastructure.scheduling.scheduler.delivery_bundle import ScheduledDeliveryAdapters
    from infrastructure.scheduling.scheduler.interactive_shell_delivery import (
        InteractiveShellScheduledDelivery,
    )
    from infrastructure.scheduling.scheduler.types import Provider
    from integrations.discord.scheduled_delivery import DiscordScheduledDelivery
    from integrations.rocketchat.scheduled_delivery import RocketChatScheduledDelivery
    from integrations.slack.scheduled_delivery import SlackScheduledDelivery
    from integrations.telegram.scheduled_delivery import TelegramScheduledDelivery

    return ScheduledDeliveryAdapters(
        {
            Provider.TELEGRAM: TelegramScheduledDelivery(),
            Provider.SLACK: SlackScheduledDelivery(),
            Provider.DISCORD: DiscordScheduledDelivery(),
            Provider.ROCKETCHAT: RocketChatScheduledDelivery(),
            Provider.INTERACTIVE_SHELL: InteractiveShellScheduledDelivery(),
        }
    )


def install_scheduled_delivery_adapters() -> None:
    """Bind the scheduled-delivery adapters (worker and CLI hosts)."""
    scheduled_delivery_adapters().install()


def install_cli_auth_checker() -> None:
    """Bind the integrations-backed CLI auth checker config reports status through.

    The integrations import is deferred to the first check, so a bare CLI
    invocation (e.g. ``opensre --help``) does not pay the subprocess-client cost.
    """
    from config.llm_auth.cli_auth import CliAuthChecker, CliAuthState

    def check(provider: str) -> CliAuthState | None:
        from integrations.llm_cli import check_cli_auth

        return check_cli_auth(provider)

    CliAuthChecker(check).install()


__all__ = [
    "install_harness_adapters",
    "install_notification_adapters",
    "scheduler_runners",
    "scheduled_delivery_adapters",
    "install_scheduled_delivery_adapters",
    "install_cli_auth_checker",
]
