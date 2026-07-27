"""Per-turn storage scope for gateway workers (Slack team installs).

Bound for the duration of a Slack turn so a store can read the turn's owner
without threading ``Principal`` through every call site. No credential store
consumes it yet: today this only makes the owner available.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar

from config.principal import Principal, StorageScope

_CURRENT_SCOPE: ContextVar[StorageScope | None] = ContextVar("opensre_storage_scope", default=None)


@contextmanager
def bound_storage_scope(scope: StorageScope) -> Iterator[StorageScope]:
    """Bind ``scope`` for the duration of the block."""
    token = _CURRENT_SCOPE.set(scope)
    try:
        yield scope
    finally:
        _CURRENT_SCOPE.reset(token)


def current_scope() -> StorageScope | None:
    """Return the bound scope, or None when nothing has been bound."""
    return _CURRENT_SCOPE.get()


def current_principal() -> Principal | None:
    """Return the principal for this turn, or None when unbound."""
    scope = _CURRENT_SCOPE.get()
    return None if scope is None else scope.principal


__all__ = ["bound_storage_scope", "current_principal", "current_scope"]
