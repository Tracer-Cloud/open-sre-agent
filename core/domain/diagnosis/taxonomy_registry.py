"""Alert-source taxonomy registry — decouples root-cause category scoping
from any one vendor.

Vendors register a :class:`TaxonomyProfile` (source matcher, scoped category
set, and prompt instruction wording) from
``integrations/harness_adapters.py``. Core owns only the default profile: the
taxonomy an alert source falls back to when no registered profile claims it.
"""

from __future__ import annotations

from typing import Protocol

from core.domain.types.root_cause_categories import (
    HERMES_ROOT_CAUSE_CATEGORIES,
    VALID_ROOT_CAUSE_CATEGORIES,
    render_prompt_taxonomy,
)

_DEFAULT_INSTRUCTION_HEADER = (
    "Use exactly one category name from the root cause taxonomy below\n\n"
    "## Root cause category taxonomy (single source of truth)"
)


class TaxonomyProfile(Protocol):
    """A vendor-scoped root-cause taxonomy: matcher, categories, prompt heading."""

    def matches(self, alert_source: str) -> bool:
        """Return True when this profile owns the given (already lowercased) alert source."""
        raise NotImplementedError

    def categories(self) -> set[str]:
        """Return the category names valid for this profile's alert sources."""
        raise NotImplementedError

    def instruction_header(self) -> str:
        """Return the prompt heading shown above the rendered taxonomy list."""
        raise NotImplementedError


_taxonomy_profiles: list[TaxonomyProfile] = []


def register_taxonomy_profile(profile: TaxonomyProfile) -> None:
    """Register a vendor taxonomy profile, checked in registration order."""
    _taxonomy_profiles.append(profile)


def clear_taxonomy_profiles() -> None:
    """Remove all registered taxonomy profiles (tests)."""
    _taxonomy_profiles.clear()


def _default_categories() -> set[str]:
    """Non-vendor-scoped fallback taxonomy for alert sources with no matching profile."""
    return set(VALID_ROOT_CAUSE_CATEGORIES - HERMES_ROOT_CAUSE_CATEGORIES)


def _profile_for_alert_source(alert_source: str) -> TaxonomyProfile | None:
    source = alert_source.strip().lower()
    for profile in _taxonomy_profiles:
        if profile.matches(source):
            return profile
    return None


def taxonomy_categories_for_alert_source(alert_source: str) -> set[str]:
    profile = _profile_for_alert_source(alert_source)
    if profile is not None:
        return set(profile.categories())
    return _default_categories()


def root_cause_category_instruction_for_source(alert_source: str) -> str:
    categories = taxonomy_categories_for_alert_source(alert_source)
    taxonomy = render_prompt_taxonomy(categories).strip()
    profile = _profile_for_alert_source(alert_source)
    header = profile.instruction_header() if profile is not None else _DEFAULT_INSTRUCTION_HEADER
    return f"{header}\n{taxonomy}"


__all__ = [
    "TaxonomyProfile",
    "clear_taxonomy_profiles",
    "register_taxonomy_profile",
    "root_cause_category_instruction_for_source",
    "taxonomy_categories_for_alert_source",
]
