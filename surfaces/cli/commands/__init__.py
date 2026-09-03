"""CLI command registration helpers."""

from __future__ import annotations

import click

from surfaces.cli.commands.account import account_command
from surfaces.cli.commands.agent import fleet
from surfaces.cli.commands.ask import ask_command
from surfaces.cli.commands.auth import auth_command
from surfaces.cli.commands.config import config_command
from surfaces.cli.commands.cron import cron_command
from surfaces.cli.commands.debug import debug_command
from surfaces.cli.commands.doctor import doctor_command
from surfaces.cli.commands.gateway import gateway_command
from surfaces.cli.commands.general import (
    health_command,
    uninstall_command,
    update_command,
    version_command,
)
from surfaces.cli.commands.guardrails import guardrails
from surfaces.cli.commands.integrations import integrations
from surfaces.cli.commands.messaging import messaging
from surfaces.cli.commands.onboard import onboard
from surfaces.cli.commands.package_smoke import package_smoke_command
from surfaces.cli.commands.posthog_report import posthog_command
from surfaces.cli.commands.remote_sync import remote_sync_command
from surfaces.cli.commands.sentry_digest import sentry_command
from surfaces.cli.commands.setup import setup_command
from surfaces.cli.commands.watchdog import watchdog_command
from surfaces.cli.commands.work import work_command

_COMMANDS: tuple[click.Command, ...] = (
    account_command,
    ask_command,
    setup_command,
    onboard,
    auth_command,
    config_command,
    integrations,
    guardrails,
    fleet,
    messaging,
    cron_command,
    sentry_command,
    posthog_command,
    watchdog_command,
    work_command,
    debug_command,
    gateway_command,
    remote_sync_command,
    health_command,
    doctor_command,
    update_command,
    uninstall_command,
    version_command,
    package_smoke_command,
)


def register_commands(cli: click.Group) -> None:
    """Attach all top-level commands to the root CLI group."""
    for command in _COMMANDS:
        cli.add_command(command)
