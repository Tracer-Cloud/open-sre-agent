"""Persistence for gateway-run investigations.

``store`` holds the contract and the process-local implementation; ``postgres``
holds the shared-queue implementation the hosted fleet runs on.
"""

from __future__ import annotations

from gateway.core.storage.investigations.postgres import PostgresInvestigationStore
from gateway.core.storage.investigations.store import (
    InMemoryInvestigationStore,
    InvestigationRecord,
    InvestigationStatus,
    InvestigationStore,
)

__all__ = [
    "InMemoryInvestigationStore",
    "InvestigationRecord",
    "InvestigationStatus",
    "InvestigationStore",
    "PostgresInvestigationStore",
]
