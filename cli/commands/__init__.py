"""CLI command registration helpers."""

from __future__ import annotations

import click

from cli.commands.agent import fleet
from cli.commands.config import config_command
from cli.commands.cron import cron_command
from cli.commands.debug import debug_command
from cli.commands.doctor import doctor_command
from cli.commands.gateway import gateway_command
from cli.commands.general import (
    health_command,
    investigate_command,
    uninstall_command,
    update_command,
    version_command,
)
from cli.commands.guardrails import guardrails
from cli.commands.hermes import hermes_command
from cli.commands.integrations import integrations
from cli.commands.messaging import messaging
from cli.commands.onboard import onboard
from cli.commands.remote import remote
from cli.commands.tests import tests
from cli.commands.watchdog import watchdog_command

_COMMANDS: tuple[click.Command, ...] = (
    investigate_command,
    onboard,
    config_command,
    remote,
    tests,
    integrations,
    guardrails,
    fleet,
    messaging,
    hermes_command,
    cron_command,
    watchdog_command,
    debug_command,
    gateway_command,
    health_command,
    doctor_command,
    update_command,
    uninstall_command,
    version_command,
)


def register_commands(cli: click.Group) -> None:
    """Attach all top-level commands to the root CLI group."""
    for command in _COMMANDS:
        cli.add_command(command)
