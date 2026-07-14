"""CLI command registration helpers."""

from __future__ import annotations

import click

from surfaces.cli.commands.agent import fleet
from surfaces.cli.commands.auth import auth_command
from surfaces.cli.commands.config import config_command
from surfaces.cli.commands.cron import cron_command
from surfaces.cli.commands.debug import debug_command
from surfaces.cli.commands.doctor import doctor_command
from surfaces.cli.commands.gateway import gateway_command
from surfaces.cli.commands.general import (
    health_command,
    investigate_command,
    uninstall_command,
    update_command,
    version_command,
)
from surfaces.cli.commands.guardrails import guardrails
from surfaces.cli.commands.hermes import hermes_command
from surfaces.cli.commands.integrations import integrations
from surfaces.cli.commands.messaging import messaging
from surfaces.cli.commands.misses import misses_command
from surfaces.cli.commands.onboard import onboard
from surfaces.cli.commands.sentry_digest import sentry_command
from surfaces.cli.commands.tests import tests
from surfaces.cli.commands.watchdog import watchdog_command

_COMMANDS: tuple[click.Command, ...] = (
    investigate_command,
    onboard,
    auth_command,
    config_command,
    tests,
    integrations,
    guardrails,
    fleet,
    messaging,
    misses_command,
    hermes_command,
    cron_command,
    sentry_command,
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
