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
# Shape tag constants                                                         #
# --------------------------------------------------------------------------- #
#
# We sort every fault category into one of three buckets that say how
# hard the category is for the LLM alone:
#
#   SHAPE_SEEN   -> easy categories (startup, runtime)
#   SHAPE_UNSEEN -> hard categories (admission, performance)
#   SHAPE_MID    -> middle categories (scheduling, service routing,
#                                      infrastructure)
#
# We give these buckets named constants instead of using ``True``,
# ``False``, and ``None`` directly in the code. The reason: in June 2026
# the case loader had a filter that asked "is the tag True or False?"
# A reader could easily think that covers everything tagged. It does
# not. Cases tagged ``None`` (mid-shape) silently fell out. Three whole
# fault categories never reached the runner. Spelling each tag out
# makes the three-way nature of the tag impossible to miss.
#
# We keep the underlying values as ``bool | None`` so the cell files
# already saved to S3 (3,177 per full corpus run) still read correctly.
# These constants are clearer names for the same values, not a new
# type.

SHAPE_SEEN: bool = True
"""Easy categories the LLM already handles well: startup, runtime."""

SHAPE_UNSEEN: bool = False
"""Hard categories where the LLM struggles most: admission, performance.
This is where opensre adds the most value."""

SHAPE_MID: None = None
"""Middle categories: scheduling, service routing, infrastructure.
Show up in the ``all`` aggregate but not in the seen-vs-unseen
comparison."""

# The case loader uses this set to spot when an operator says "I want
# both labels" (the most common intent: give me everything tagged).
# When the filter matches this set, the loader skips the filter
# entirely so mid-shape cases also pass through. Without this rule the
# filter dropped every mid-shape case and capped the corpus at 78%.
ALL_LABELED_SHAPES: frozenset[bool] = frozenset({SHAPE_SEEN, SHAPE_UNSEEN})


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
    """Map a fault category to its shape tag.

    Returns one of the three constants defined at the top of this module:
        ``SHAPE_SEEN``   — Easy faults; explicit signals.
        ``SHAPE_UNSEEN`` — Hard faults; symptoms decoupled from cause.
        ``SHAPE_MID``    — Medium faults; not in seen/unseen aggregates.
    """
    key = _normalize(fault_category)
    if key in _SEEN_SHAPE_CATEGORIES:
        return SHAPE_SEEN
    if key in _UNSEEN_SHAPE_CATEGORIES:
        return SHAPE_UNSEEN
    if key in _MID_SHAPE_CATEGORIES:
        return SHAPE_MID
    # Unknown category — treat as mid-shape to avoid silently
    # mis-stratifying. Surfaced through the framework's reporting layer
    # which will list `all` only for unrecognized categories.
    return SHAPE_MID


def known_categories() -> dict[str, bool | None]:
    """For introspection and CLI display: every category we recognize."""
    out: dict[str, bool | None] = {}
    for cat in _SEEN_SHAPE_CATEGORIES:
        out[cat] = SHAPE_SEEN
    for cat in _UNSEEN_SHAPE_CATEGORIES:
        out[cat] = SHAPE_UNSEEN
    for cat in _MID_SHAPE_CATEGORIES:
        out[cat] = SHAPE_MID
    return out
