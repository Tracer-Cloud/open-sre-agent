"""Outbound notification adapter registry, mirroring
:mod:`infrastructure.delivery.reporting.delivery_registry`.

A sibling of the report registry rather than a reuse of it. Rocket.Chat refuses a
webhook fallback here but takes it there, and an SMTP adapter cannot join the
report registry at all: its bootstrap runs on every ``dispatch_report`` and would
email on every foreground investigation.

There is deliberately no ``iter_`` function. The report path fans one report out to
every registered vendor; this path delivers to an ordered list of channels the user
chose, and ``/background show`` renders the result mapping in that order, so
resolving by name keeps the caller's ordering authoritative.

Mutation is an unsynchronised module dict, matching the report registry. The sole
caller runs on a daemon thread, but dict writes are atomic under the GIL and
CPython's per-module import lock serialises the adapter imports, so the race is
benign.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from infrastructure.scheduling.background_investigations.types import BackgroundInvestigationRecord

ChannelName = str

# Capability advertised by adapters that can deliver a background-RCA
# completion notice. Adapters declare it rather than the dispatcher keeping a
# curated list of channel names.
BACKGROUND_RCA = "background_rca"


@runtime_checkable
class OutboundNotificationAdapter(Protocol):
    """Contract every outbound notification channel must satisfy.

    Safe to call whenever the channel was requested: a missing credential or
    destination returns a ``"missing ..."`` string rather than raising, because
    the result lands on the record and is shown by ``/background show``.
    """

    name: ChannelName
    capabilities: frozenset[str]

    def deliver(self, record: BackgroundInvestigationRecord) -> str:
        """Deliver ``record`` to this channel and return the outcome string.

        Returns ``"sent"`` on success, ``"failed: <reason>"`` when the transport
        rejected the message, or ``"missing <channel> integration: ..."`` when
        the channel is not configured. Any credential in the reason must be
        redacted before it is returned: the string is persisted on the record.
        """


_adapters: dict[ChannelName, OutboundNotificationAdapter] = {}


def register_outbound_adapter(adapter: OutboundNotificationAdapter) -> None:
    """Register ``adapter`` under its declared :attr:`name`.

    Idempotent: re-registering the same name replaces the previous entry, so
    tests can inject stubs.
    """
    _adapters[adapter.name] = adapter


def get_outbound_adapter(name: ChannelName) -> OutboundNotificationAdapter | None:
    """Return the adapter registered under ``name``, or ``None``."""
    return _adapters.get(name)


def registered_outbound_adapter_names() -> tuple[ChannelName, ...]:
    """Return the names of currently registered adapters (for diagnostics)."""
    return tuple(_adapters.keys())


def outbound_adapter_names_for(capability: str) -> tuple[ChannelName, ...]:
    """Return the sorted names of adapters advertising ``capability``."""
    return tuple(sorted(name for name, a in _adapters.items() if capability in a.capabilities))


def clear_outbound_adapters() -> None:
    """Drop every registered adapter (test isolation helper)."""
    _adapters.clear()


__all__ = [
    "BACKGROUND_RCA",
    "ChannelName",
    "OutboundNotificationAdapter",
    "clear_outbound_adapters",
    "get_outbound_adapter",
    "outbound_adapter_names_for",
    "register_outbound_adapter",
    "registered_outbound_adapter_names",
]
