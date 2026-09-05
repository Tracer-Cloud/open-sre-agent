"""Provider-neutral values used to retrieve and select operational runbooks."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Literal, Protocol

RunbookSelectionStatus = Literal["matched", "not_found", "ambiguous"]
RunbookSelectionReason = Literal["alertname_labels", "alertname", "service", "none"]


@dataclass(frozen=True, slots=True)
class IncidentIdentity:
    """Stable alert fields used for deterministic runbook selection."""

    alertname: str = ""
    service: str = ""
    labels: tuple[tuple[str, str], ...] = ()

    @classmethod
    def from_values(
        cls,
        *,
        alertname: str = "",
        service: str = "",
        labels: Mapping[str, object] | None = None,
    ) -> IncidentIdentity:
        """Normalize user and alert fields into an immutable identity."""
        normalized_labels = tuple(
            sorted(
                (str(key).strip(), str(value).strip())
                for key, value in (labels or {}).items()
                if str(key).strip() and str(value).strip()
            )
        )
        return cls(
            alertname=alertname.strip(),
            service=service.strip(),
            labels=normalized_labels,
        )

    @property
    def labels_by_name(self) -> dict[str, str]:
        """Return labels as a lookup map."""
        return dict(self.labels)


@dataclass(frozen=True, slots=True)
class RunbookMatch:
    """Exact incident fields that select one catalog entry."""

    alertname: str = ""
    service: str = ""
    labels: tuple[tuple[str, str], ...] = ()


@dataclass(frozen=True, slots=True)
class RunbookCatalogEntry:
    """One manifest entry and its deterministic match rule."""

    document_id: str
    path: str
    title: str = ""
    match: RunbookMatch = field(default_factory=RunbookMatch)


@dataclass(frozen=True, slots=True)
class RunbookReference:
    """Provider-neutral pointer to one runbook document."""

    source_name: str
    document_id: str
    path: str
    requested_revision: str = ""
    canonical_url: str = ""


@dataclass(frozen=True, slots=True)
class RunbookDocument:
    """Bounded runbook content retrieved at an immutable revision."""

    reference: RunbookReference
    content: str
    resolved_revision: str
    source_uri: str
    title: str = ""
    truncated: bool = False


@dataclass(frozen=True, slots=True)
class RunbookSelection:
    """Result of matching incident identity against a runbook catalog."""

    status: RunbookSelectionStatus
    entry: RunbookCatalogEntry | None = None
    reason: RunbookSelectionReason = "none"
    matched_fields: tuple[str, ...] = ()
    candidate_ids: tuple[str, ...] = ()


class RunbookSource(Protocol):
    """Provider that verifies and retrieves runbook documents."""

    provider: str

    def verify(self, source_name: str) -> tuple[bool, str]:
        """Verify that the configured source can retrieve runbooks."""

    def fetch_document(self, reference: RunbookReference) -> RunbookDocument:
        """Retrieve one bounded document at an immutable source revision."""


__all__ = [
    "IncidentIdentity",
    "RunbookCatalogEntry",
    "RunbookDocument",
    "RunbookMatch",
    "RunbookReference",
    "RunbookSelection",
    "RunbookSelectionReason",
    "RunbookSelectionStatus",
    "RunbookSource",
]
