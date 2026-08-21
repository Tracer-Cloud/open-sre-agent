"""Port for probing a local CLI provider's auth, filled once at boot.

``config.llm_auth.credentials`` reports prompt-safe auth status without importing
``integrations``; the integrations-backed prober is bundled at the composition
root (``bootstrap.adapters``) and installed once, mirroring
:class:`infrastructure.scheduling.scheduler.delivery_bundle.ScheduledDeliveryAdapters`.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable


@dataclass(frozen=True)
class CliAuthProbe:
    """Local install/auth state for one CLI provider."""

    installed: bool
    logged_in: bool | None
    detail: str


@runtime_checkable
class ProbeCliAuth(Protocol):
    """Probes a CLI provider's local auth."""

    def __call__(self, provider: str) -> CliAuthProbe | None:
        """Return the provider's local-auth probe, or ``None`` if no adapter exists."""


@dataclass(frozen=True)
class CliAuthProber:
    """Immutable CLI-auth prober, installed once at boot."""

    probe: ProbeCliAuth

    def install(self) -> None:
        """Bind this prober as the process-wide CLI-auth prober."""
        global _installed
        _installed = self


_installed: CliAuthProber | None = None


def resolve_cli_auth_probe(provider: str) -> CliAuthProbe | None:
    """Return the installed prober's result for ``provider``.

    ``None`` when no prober is installed or the provider has no CLI adapter.
    """
    return _installed.probe(provider) if _installed is not None else None


__all__ = ["CliAuthProbe", "CliAuthProber", "ProbeCliAuth", "resolve_cli_auth_probe"]
