"""Shared prerequisites for PostHog metric report automation."""

from __future__ import annotations

from rich.console import Console

from platform.harness_ports import configured_integration_services
from platform.scheduler.delivery import delivery_provider_ready, delivery_setup_hint
from platform.scheduler.types import Provider

_console = Console()

POSTHOG_SERVICE_NAMES: tuple[str, ...] = ("posthog_mcp",)

DEFAULT_POSTHOG_PERIOD = "7d"

_NOT_CONFIGURED_HINT = (
    "PostHog MCP is not configured. Run `opensre integrations setup` and verify "
    "with `opensre integrations verify posthog_mcp` before requesting a report."
)


def posthog_report_available() -> bool:
    """Return True when a PostHog data source that can serve the skill is configured."""
    configured = configured_integration_services()
    return any(name in configured for name in POSTHOG_SERVICE_NAMES)


def posthog_not_configured_hint() -> str:
    """Human-readable guidance shown when no PostHog data source is configured."""
    return _NOT_CONFIGURED_HINT


def require_posthog_integration() -> None:
    """Exit when PostHog is not configured."""
    if posthog_report_available():
        return
    _console.print(f"[red]{_NOT_CONFIGURED_HINT}[/red]")
    raise SystemExit(1)


def require_report_delivery_provider(provider: str) -> None:
    """Exit when the chosen delivery provider is not configured."""
    provider_enum = Provider(provider)
    if delivery_provider_ready(provider_enum):
        return
    _console.print(f"[red]{delivery_setup_hint(provider_enum)}[/red]")
    raise SystemExit(1)


__all__ = [
    "DEFAULT_POSTHOG_PERIOD",
    "POSTHOG_SERVICE_NAMES",
    "posthog_not_configured_hint",
    "posthog_report_available",
    "require_posthog_integration",
    "require_report_delivery_provider",
]
