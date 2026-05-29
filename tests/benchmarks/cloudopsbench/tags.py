"""Seen-shape vs unseen-shape tagging for Cloud-OpsBench cases.

The tagging rule comes from the paper's empirical performance stratification
(Wang et al, arXiv:2603.00468v1, Table 4 and Fig 3):

  - **Easy** faults (Startup, Runtime) — A@1 > 0.65 universally; explicit
    signals like CrashLoopBackOff/OOMKilled directly name the cause. These
    are the "seen-shape" cases: opensre+LLM and LLM-alone both do well here,
    so opensre's structural value should show smaller lift.

  - **Hard** faults (Admission Control, Performance) — A@1 < 0.36 universally;
    symptoms are decoupled from root cause, requiring cross-layer reasoning.
    These are the "unseen-shape" cases: where Vincent's "performs worse on
    unseen situations" concern bites, and where opensre's stage-gated
    investigation should add the most value.

  - **Medium** faults (Scheduling, Service Routing, Infrastructure) — A@1
    between 0.4-0.6. Mid-shape. Not classified for now (returns None) so
    seen/unseen aggregates aren't diluted; they still appear in `all`.

This stratification sidesteps subjective tagging — opensre's lift on
unseen-shape vs seen-shape becomes the empirical anti-overfit gate
(per ``framework.md`` § 14: "opensre's lift on unseen-shape must be
≥ lift on seen-shape").
"""

from __future__ import annotations

# --------------------------------------------------------------------------- #
# Mapping                                                                     #
# --------------------------------------------------------------------------- #

# Fault categories the Cloud-OpsBench corpus uses, from the paper Table 2:
#   Admission Control, Scheduling, Startup, Runtime,
#   Service Routing, Performance, Infrastructure
#
# The directory names in the HF dataset are lowercased (e.g. "admission",
# "service_routing"); _normalize() folds aliases to canonical strings.

_SEEN_SHAPE_CATEGORIES: frozenset[str] = frozenset(
    {
        "startup",
        "runtime",
    }
)

_UNSEEN_SHAPE_CATEGORIES: frozenset[str] = frozenset(
    {
        "admission",
        "admission_control",
        "performance",
    }
)

_MID_SHAPE_CATEGORIES: frozenset[str] = frozenset(
    {
        "scheduling",
        "service",  # HF dataset uses bare "service" for the Service Routing category
        "service_routing",
        "service-routing",
        "infrastructure",
        "infra",
    }
)


def _normalize(fault_category: str) -> str:
    """Lowercase + replace separators so legacy variants match."""
    return fault_category.strip().lower().replace("-", "_").replace(" ", "_")


def seen_shape_for(fault_category: str) -> bool | None:
    """Map a fault category to its seen/unseen tag.

    Returns:
        True   — seen-shape (Easy faults; explicit signals)
        False  — unseen-shape (Hard faults; symptoms decoupled from root cause)
        None   — mid-shape (Medium faults; not classified — appears in `all`
                 stratum but not in seen/unseen aggregates)
    """
    key = _normalize(fault_category)
    if key in _SEEN_SHAPE_CATEGORIES:
        return True
    if key in _UNSEEN_SHAPE_CATEGORIES:
        return False
    if key in _MID_SHAPE_CATEGORIES:
        return None
    # Unknown category — treat as mid-shape (None) to avoid silently
    # mis-stratifying. Surfaced through the framework's reporting layer
    # which will list `all` only for unrecognized categories.
    return None


def known_categories() -> dict[str, bool | None]:
    """For introspection and CLI display: every category we recognize."""
    out: dict[str, bool | None] = {}
    for cat in _SEEN_SHAPE_CATEGORIES:
        out[cat] = True
    for cat in _UNSEEN_SHAPE_CATEGORIES:
        out[cat] = False
    for cat in _MID_SHAPE_CATEGORIES:
        out[cat] = None
    return out
