"""Report-delivery adapter registry shared by ``tools`` and ``integrations``.

The investigation pipeline used to hard-code six vendor imports inside
``tools/investigation/reporting/delivery/dispatch.py``. Under the T-4 strict
layering rules (issue #3352), ``tools`` must not import from ``integrations``
directly. This module defines the neutral seam:

* :class:`ReportDeliveryAdapter` — the protocol every vendor adapter satisfies.
* :func:`register_delivery_adapter` — vendor adapter modules call this at
  import time to advertise themselves.
* :func:`iter_delivery_adapters` — the dispatch loop iterates over registered
  adapters and lets each decide whether the current investigation state has
  enough context to deliver.

Registration is process-scoped and idempotent (re-registering the same name
replaces the prior entry). Adapters are ordered by insertion; tests can clear
the registry via :func:`clear_delivery_adapters`.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any, Protocol, runtime_checkable

VendorName = str

# The shape passed to every adapter. Kept as a plain ``dict`` alias so the
# pipeline can drop new keys without every vendor package needing an update.
DeliveryContext = Mapping[str, Any]


@runtime_checkable
class ReportDeliveryAdapter(Protocol):
    """Contract every vendor delivery adapter must satisfy.

    Adapters are expected to be safe to call unconditionally: if the current
    investigation state does not carry the credentials or context needed to
    deliver, :meth:`deliver` should log a debug line and return ``False``. The
    dispatch loop treats ``False`` as "adapter skipped" and continues.
    """

    name: VendorName

    def deliver(
        self,
        state: DeliveryContext,
        *,
        messages: DeliveryContext,
        blocks: list[dict[str, Any]],
    ) -> bool:
        """Deliver the rendered report to this vendor's channel.

        ``state`` is the raw investigation state (typed as
        :class:`core.state.InvestigationState` at the call site).
        ``messages`` carries the pre-rendered per-channel payloads
        (``slack_text``, ``telegram_html`` …). ``blocks`` is the shared list of
        Slack Block Kit blocks that vendors may reuse for interactive replies.

        Return ``True`` when a delivery was attempted (successful or not — the
        adapter is expected to log its own failures), ``False`` when the
        adapter chose to skip (no credentials, missing context, etc.).
        """


_adapters: dict[VendorName, ReportDeliveryAdapter] = {}


def register_delivery_adapter(adapter: ReportDeliveryAdapter) -> None:
    """Register ``adapter`` under its declared :attr:`name`.

    Registration is idempotent — re-registering an adapter with the same name
    replaces the previous entry so tests can inject stubs and vendors can hot-
    swap implementations without leaking state across processes.
    """
    _adapters[adapter.name] = adapter


def iter_delivery_adapters() -> Iterable[ReportDeliveryAdapter]:
    """Return every registered adapter in insertion order.

    Callers should treat the returned iterable as a snapshot; modifying the
    registry during iteration is not supported.
    """
    return tuple(_adapters.values())


def get_delivery_adapter(name: VendorName) -> ReportDeliveryAdapter | None:
    """Return the adapter registered under ``name``, or ``None``."""
    return _adapters.get(name)


def registered_delivery_adapter_names() -> tuple[VendorName, ...]:
    """Return the names of currently registered adapters (for diagnostics)."""
    return tuple(_adapters.keys())


def clear_delivery_adapters() -> None:
    """Drop every registered adapter (test isolation helper)."""
    _adapters.clear()


__all__ = [
    "DeliveryContext",
    "ReportDeliveryAdapter",
    "VendorName",
    "clear_delivery_adapters",
    "get_delivery_adapter",
    "iter_delivery_adapters",
    "register_delivery_adapter",
    "registered_delivery_adapter_names",
]
