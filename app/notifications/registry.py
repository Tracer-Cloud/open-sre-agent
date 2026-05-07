"""Registry for notification providers."""

from __future__ import annotations

import logging
from typing import Any, TYPE_CHECKING

if TYPE_CHECKING:
    from app.notifications.interface import NotificationProvider

logger = logging.getLogger(__name__)


class NotificationRegistry:
    """Manages available notification providers."""

    def __init__(self) -> None:
        self._providers: dict[str, NotificationProvider] = {}

    def register(self, name: str, provider: NotificationProvider) -> None:
        """Register a notification provider."""
        self._providers[name] = provider
        logger.debug("Registered notification provider: %s", name)

    def get_provider(self, name: str) -> NotificationProvider | None:
        """Retrieve a specific provider by name."""
        return self._providers.get(name)

    def get_providers(self) -> dict[str, NotificationProvider]:
        """Get all registered providers."""
        return self._providers


# Global registry instance
_registry = NotificationRegistry()


def get_registry() -> NotificationRegistry:
    """Get the global notification registry."""
    # Ensure providers are discovered on first access
    if not _registry.get_providers():
        discover_providers()
    return _registry


def discover_providers() -> None:
    """
    Discover and register all built-in notification providers.
    """
    from app.notifications.providers.whatsapp import WhatsAppProvider
    from app.notifications.providers.telegram import TelegramProvider
    from app.notifications.providers.discord import DiscordProvider
    from app.notifications.providers.slack import SlackProvider

    _registry.register("whatsapp", WhatsAppProvider())
    _registry.register("telegram", TelegramProvider())
    _registry.register("discord", DiscordProvider())
    _registry.register("slack", SlackProvider())
