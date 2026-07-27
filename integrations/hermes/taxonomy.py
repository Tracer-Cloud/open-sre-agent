"""Hermes agent-runtime root-cause taxonomy profile.

Registered from ``integrations/harness_adapters.py`` so the diagnosis stage
scopes root-cause categories to the Hermes agent-runtime taxonomy only when
the alert originates from Hermes.
"""

from __future__ import annotations

from dataclasses import dataclass

from core.domain.diagnosis.taxonomy_registry import TaxonomyProfile
from core.domain.types.root_cause_categories import HERMES_ROOT_CAUSE_CATEGORIES

_INSTRUCTION_HEADER = (
    "Use exactly one category name from the Hermes taxonomy below\n\n"
    "## Hermes root cause category taxonomy (single source of truth)"
)


@dataclass(frozen=True)
class HermesTaxonomyProfile(TaxonomyProfile):
    """Root-cause taxonomy scoped to Hermes agent-runtime categories."""

    def matches(self, alert_source: str) -> bool:
        return alert_source == "hermes"

    def categories(self) -> set[str]:
        return set(HERMES_ROOT_CAUSE_CATEGORIES | {"healthy", "unknown"})

    def instruction_header(self) -> str:
        return _INSTRUCTION_HEADER


HERMES_TAXONOMY_PROFILE = HermesTaxonomyProfile()

__all__ = ["HERMES_TAXONOMY_PROFILE", "HermesTaxonomyProfile"]
