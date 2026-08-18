"""Persistence for gateway-run investigations.

``store`` holds the contract, the process-local implementation and the
shared-queue Postgres implementation the hosted fleet runs on.
"""

from __future__ import annotations

from gateway.core.storage.investigations.repository import (
    InMemoryInvestigationRepository,
    InvestigationRecord,
    InvestigationRepository,
    InvestigationStatus,
    PostgresInvestigationRepository,
)

__all__ = [
    "InMemoryInvestigationRepository",
    "InvestigationRecord",
    "InvestigationStatus",
    "InvestigationRepository",
    "PostgresInvestigationRepository",
]
