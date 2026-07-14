"""Shared prerequisites for Sentry morning digest automation."""

from __future__ import annotations

from rich.console import Console

from integrations.sentry.digest_delivery import (
    delivery_provider_ready,
    digest_delivery_setup_hint,
)
from platform.harness_ports import configured_integration_services
from platform.scheduler.types import Provider

_console = Console()


def require_sentry_integration() -> None:
    """Exit when Sentry is not configured."""
    if "sentry" in configured_integration_services():
        return
    _console.print(
        "[red]Sentry is not configured.[/red] Run `opensre integrations setup` and verify "
        "with `opensre integrations verify sentry` before scheduling a digest."
    )
    raise SystemExit(1)


def require_digest_delivery_provider(provider: str) -> None:
    """Exit when the chosen delivery provider is not configured."""
    provider_enum = Provider(provider)
    if delivery_provider_ready(provider_enum):
        return
    _console.print(f"[red]{digest_delivery_setup_hint(provider_enum)}[/red]")
    raise SystemExit(1)


__all__ = ["require_digest_delivery_provider", "require_sentry_integration"]
