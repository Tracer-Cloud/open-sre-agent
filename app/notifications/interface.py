"""Base interface for notification providers."""

from __future__ import annotations

from abc import ABC, abstractmethod
from app.notifications.models import NotificationEvent


class NotificationProvider(ABC):
    """Abstract base class for all notification providers."""

    @abstractmethod
    def send_notification(
        self,
        event: NotificationEvent,
        config: dict[str, Any],
    ) -> tuple[bool, str]:
        """
        Send a notification event to the provider.

        Args:
            event: The structured notification event.
            config: Provider-specific configuration (credentials, targets).

        Returns:
            (success, error_detail)
        """
        pass

    @abstractmethod
    def probe(self, config: dict[str, Any]) -> tuple[bool, str]:
        """
        Verify connectivity to the provider.

        Args:
            config: The provider configuration dictionary.

        Returns:
            (success, detail) — success is True if connectivity is verified.
        """
        pass
