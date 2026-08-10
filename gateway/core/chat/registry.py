"""Registry for chat notification implementations by platform."""

from __future__ import annotations

import threading

from gateway.core.chat.notifier import ChatNotifier


class ChatNotifierRegistry:
    """Thread-safe registry of chat notifiers by platform name."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._notifiers: dict[str, ChatNotifier] = {}

    def register(self, platform: str, notifier: ChatNotifier) -> None:
        """Register a notifier for the given platform (e.g., 'slack')."""
        with self._lock:
            self._notifiers[platform] = notifier

    def get(self, platform: str) -> ChatNotifier | None:
        """Get notifier for platform, or None if not registered."""
        with self._lock:
            return self._notifiers.get(platform)

    def list_platforms(self) -> list[str]:
        """Return list of registered platform names."""
        with self._lock:
            return list(self._notifiers.keys())


# Process-global registry instance
_registry_lock = threading.Lock()
_registry: ChatNotifierRegistry | None = None


def get_chat_notifier_registry() -> ChatNotifierRegistry:
    """Get the process-global chat notifier registry."""
    global _registry
    if _registry is None:
        with _registry_lock:
            if _registry is None:
                _registry = ChatNotifierRegistry()
    return _registry


def get_chat_notifier(platform: str) -> ChatNotifier | None:
    """Get notifier for platform, or None if not registered."""
    return get_chat_notifier_registry().get(platform)


def reset_chat_notifier_registry_for_tests() -> None:
    """Drop every registered notifier so a test cannot leak one into the next.

    Registration is process-global and permanent by design — a transport
    registers once at startup. A test that registers Slack and does not undo it
    makes every later "no notifier" assertion pass for the wrong reason.
    """
    global _registry
    with _registry_lock:
        _registry = None
