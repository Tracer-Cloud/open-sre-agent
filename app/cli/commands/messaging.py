"""CLI commands for messaging security: DM pairing and identity management."""

from __future__ import annotations

import time

import click
from rich.console import Console

from app.integrations.messaging_security import (
    MessagingPlatform,
    generate_pairing_code,
    hash_pairing_code,
    load_identity_policy,
    save_identity_policy,
)

_console = Console(highlight=False)

_PLATFORM_CHOICES = [p.value for p in MessagingPlatform]


@click.group("messaging")
def messaging() -> None:
    """Messaging security: DM pairing and identity management."""


@messaging.command("pair")
@click.option(
    "--platform",
    "-p",
    type=click.Choice(_PLATFORM_CHOICES, case_sensitive=False),
    required=True,
    help="Messaging platform to pair with.",
)
def pair_command(platform: str) -> None:
    """Generate a one-time pairing code for DM authentication.

    The operator runs this command, receives a code, and sends it to the
    bot via DM as `/pair <code>`. On success the bot adds the sender to
    the allowed-users list.
    """
    service = platform.lower()
    record, policy = load_identity_policy(service)

    was_disabled = not policy.inbound_enabled

    # Generate pairing code
    code = generate_pairing_code()
    policy.pairing_secret_hash = hash_pairing_code(code)
    policy.pairing_created_at = time.time()
    policy.pairing_attempts = 0
    policy.require_dm_pairing = True
    policy.inbound_enabled = True

    save_identity_policy(service, record, policy)

    if was_disabled:
        _console.print(f"[yellow]Note: inbound messaging has been enabled for {platform}.[/yellow]")
    _console.print(f"\n[bold green]Pairing code generated for {platform}:[/bold green]")
    _console.print(f"\n  [bold yellow]{code}[/bold yellow]\n")
    _console.print(f"Send this to the bot via DM: [dim]/pair {code}[/dim]")
    _console.print("[dim]The code is single-use and will expire in 15 minutes.[/dim]\n")


@messaging.command("allow")
@click.option(
    "--platform",
    "-p",
    type=click.Choice(_PLATFORM_CHOICES, case_sensitive=False),
    required=True,
    help="Messaging platform.",
)
@click.option(
    "--user-id",
    "-u",
    required=True,
    help="Platform-native user ID to add to the allowed list.",
)
def allow_command(platform: str, user_id: str) -> None:
    """Manually add a user to the allowed-users list (bypasses DM pairing)."""
    service = platform.lower()
    record, policy = load_identity_policy(service)

    if user_id in policy.allowed_user_ids:
        _console.print(
            f"[yellow]User {user_id} is already in the allowed list for {platform}.[/yellow]"
        )
        return

    was_disabled = not policy.inbound_enabled
    policy.allowed_user_ids.append(user_id)
    policy.inbound_enabled = True
    save_identity_policy(service, record, policy)

    if was_disabled:
        _console.print(f"[yellow]Note: inbound messaging has been enabled for {platform}.[/yellow]")

    _console.print(f"[green]Added user {user_id} to {platform} allowed list.[/green]")


@messaging.command("revoke")
@click.option(
    "--platform",
    "-p",
    type=click.Choice(_PLATFORM_CHOICES, case_sensitive=False),
    required=True,
    help="Messaging platform.",
)
@click.option(
    "--user-id",
    "-u",
    required=True,
    help="Platform-native user ID to remove from the allowed list.",
)
def revoke_command(platform: str, user_id: str) -> None:
    """Remove a user from the allowed-users list."""
    service = platform.lower()
    record, policy = load_identity_policy(service)

    if user_id not in policy.allowed_user_ids:
        _console.print(
            f"[yellow]User {user_id} is not in the allowed list for {platform}.[/yellow]"
        )
        return

    policy.allowed_user_ids.remove(user_id)
    # Clear any pending pairing code so the revoked user cannot re-pair via a
    # code that was generated after their revocation.
    policy.pairing_secret_hash = None
    policy.pairing_created_at = None
    policy.pairing_attempts = 0
    save_identity_policy(service, record, policy)

    _console.print(f"[green]Removed user {user_id} from {platform} allowed list.[/green]")


@messaging.command("status")
@click.option(
    "--platform",
    "-p",
    type=click.Choice(_PLATFORM_CHOICES, case_sensitive=False),
    required=True,
    help="Messaging platform.",
)
def status_command(platform: str) -> None:
    """Show the current messaging security status for a platform."""
    service = platform.lower()
    record, policy = load_identity_policy(service)

    _console.print(f"\n[bold]Messaging Security Status — {platform}[/bold]\n")

    if record is None:
        _console.print(f"[yellow]No {platform} integration configured.[/yellow]")
        _console.print("[dim]Run the setup wizard or configure the integration first.[/dim]\n")
        return

    _console.print(f"  Inbound enabled:     {'Yes' if policy.inbound_enabled else 'No'}")
    _console.print(f"  DM pairing required: {'Yes' if policy.require_dm_pairing else 'No'}")
    _console.print(f"  Pairing pending:     {'Yes' if policy.pairing_secret_hash else 'No'}")
    _console.print(f"  Rejection behavior:  {policy.rejection_behavior.value}")
    _console.print(f"  Allowed users:       {len(policy.allowed_user_ids)}")
    if policy.allowed_user_ids:
        for uid in policy.allowed_user_ids:
            _console.print(f"    - {uid}")
    _console.print(f"  Allowed chats:       {len(policy.allowed_chat_ids)}")
    if policy.allowed_chat_ids:
        for cid in policy.allowed_chat_ids:
            _console.print(f"    - {cid}")
    _console.print()
