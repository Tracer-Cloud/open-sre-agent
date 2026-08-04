"""Whether the configured store is publicly readable — an optional provider capability.

Kept apart from :mod:`platform.filestorage.ports` on purpose: the engine's
``ObjectStore`` protocol stays at its four operations for every backend.
Answering "can anyone on the internet read this?" needs vendor-specific APIs
(S3's ``GetBucketPolicyStatus`` today) that most providers, and every
community backend, will never implement — so a provider opts in by
registering a checker (see
:func:`platform.filestorage.providers.registry.register_object_store`)
instead of the protocol growing a fifth method every backend must satisfy.

A checker is never load-bearing: it degrades to
:class:`~platform.filestorage.enums.BucketExposure.UNKNOWN` rather than
raising, so a stalled or denied exposure check never blocks ``status`` or
``setup`` — only ``PUBLIC`` is worth interrupting anyone's day for.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from platform.filestorage.enums import BucketExposure


@dataclass(frozen=True)
class PublicAccessStatus:
    """Best-effort answer to "is this store publicly readable?"

    ``detail`` is empty for a definitive ``PRIVATE``/``PUBLIC`` result and
    carries a short, operator-facing reason whenever ``exposure`` is
    ``UNKNOWN``.
    """

    exposure: BucketExposure
    detail: str = ""


PublicAccessChecker = Callable[["RemoteSyncConfig"], PublicAccessStatus]


def not_checked(provider: str) -> PublicAccessStatus:
    """Result for a provider that has not registered an exposure checker."""
    return PublicAccessStatus(
        BucketExposure.UNKNOWN, f"public-access check not implemented for provider {provider!r}"
    )


__all__ = ["PublicAccessChecker", "PublicAccessStatus", "not_checked"]
