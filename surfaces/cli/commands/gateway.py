"""Local gateway commands."""

from __future__ import annotations

import click


@click.group(name="gateway")
def gateway_command() -> None:
    """Run OpenSRE gateway servers (Telegram chat)."""


@gateway_command.command("telegram")
def gateway_telegram_command() -> None:
    """Run the Telegram two-way messaging gateway."""
    click.echo("Starting Telegram gateway (long-poll mode)")
    from gateway.manager import start_gateway

    start_gateway()
