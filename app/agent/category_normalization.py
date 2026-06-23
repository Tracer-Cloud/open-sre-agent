"""Normalize free-text root cause categories onto the product taxonomy."""

from __future__ import annotations

import logging
import re

from app.types.root_cause_categories import (
    HERMES_ROOT_CAUSE_CATEGORIES,
    VALID_ROOT_CAUSE_CATEGORIES,
    render_prompt_taxonomy,
)

logger = logging.getLogger(__name__)

_TOKEN_RE = re.compile(r"[\s\-/]+")

# Hand-curated synonym map: keys are normalized tokens (see ``_normalize_token``),
# values are canonical product taxonomy names. Not fuzzy matching — each entry is an
# intentional adjacent label (e.g. legacy coarse buckets). Targets are still gated by
# ``allowed_categories`` at lookup time (Hermes vs non-Hermes).
_CATEGORY_ALIASES: dict[str, str] = {
    "code_bug": "code_defect_null_handling",
    "config_error": "configuration_error",
    "configuration": "configuration_error",
    "connection_pool_exhaustion": "connection_exhaustion",
    "cpu_throttling": "pod_cpu_throttled",
    "database": "connection_exhaustion",
    "database_connection_failure": "connection_exhaustion",
    "dns_failure": "dns_resolution_failure",
    "infrastructure": "configuration_error",
    "memory_pressure": "pod_oomkilled",
    "mysql_connection_pool_exhaustion": "connection_pool_leak",
    "network_delay": "network_partition",
    "network_latency_issue": "network_partition",
    "oom_killed": "pod_oomkilled",
    "oomkilled": "pod_oomkilled",
    "performance": "application_tier_load_spike",
    "pod_cpu_overload": "pod_cpu_throttled",
    "pod_oom_killed": "pod_oomkilled",
    "redis_connection_pool_exhaustion": "connection_pool_leak",
}


def _normalize_token(raw: str) -> str:
    cleaned = raw.strip().lower()
    return _TOKEN_RE.sub("_", cleaned).strip("_")


def taxonomy_categories_for_alert_source(alert_source: str) -> set[str]:
    source = alert_source.strip().lower()
    if source == "hermes":
        return set(HERMES_ROOT_CAUSE_CATEGORIES | {"healthy", "unknown"})
    return set(VALID_ROOT_CAUSE_CATEGORIES - HERMES_ROOT_CAUSE_CATEGORIES)


def root_cause_category_instruction_for_source(alert_source: str) -> str:
    categories = taxonomy_categories_for_alert_source(alert_source)
    taxonomy = render_prompt_taxonomy(categories).strip()
    if alert_source.strip().lower() == "hermes":
        return (
            "Use exactly one category name from the Hermes taxonomy below\n\n"
            "## Hermes root cause category taxonomy (single source of truth)\n"
            f"{taxonomy}"
        )
    return (
        "Use exactly one category name from the root cause taxonomy below\n\n"
        "## Root cause category taxonomy (single source of truth)\n"
        f"{taxonomy}"
    )


def normalize_root_cause_category(raw: str, *, allowed_categories: set[str]) -> str:
    """Map adjacent labels onto a canonical allowed category when possible.

    Resolution order:
    1. Exact match in ``allowed_categories`` (canonical passthrough).
    2. Token-normalize (lowercase, spaces/hyphens/slashes → underscores), retry exact match.
    3. Alias lookup on the normalized token; apply only if the alias target is allowed.
    4. Otherwise return the original trimmed string unchanged.
    """
    cleaned = raw.strip()
    if not cleaned:
        return cleaned

    if cleaned in allowed_categories:
        return cleaned

    normalized = _normalize_token(cleaned)
    if normalized in allowed_categories:
        return normalized

    alias_target = _CATEGORY_ALIASES.get(normalized)
    if alias_target is not None and alias_target in allowed_categories:
        logger.info("Normalized root_cause_category %r -> %r", cleaned, alias_target)
        return alias_target

    return cleaned
