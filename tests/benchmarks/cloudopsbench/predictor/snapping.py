"""Lever A — controlled-vocabulary snapping.

The scorer (``scoring.compare_prediction``) requires an EXACT match, after
lower-case + strip, against the dataset's canonical tokens. Failure
analysis of the 2026-06-05 run showed 62% of a1=0 cases emitted a
root_cause that is not in the dataset vocabulary at all — including pure
drift like ``missing_secrectbinding`` (→ missing_secret_binding) and
``network_packet_loss`` (→ node_network_packet_loss). Those auto-fail no
matter how good the diagnosis was. We snap the model's output back onto
the closed vocabulary before scoring. Snapping only ever moves a token
CLOSER to a canonical value, so it cannot regress a previously-passing
case; if nothing is close enough, the original cleaned string is kept.
"""

from __future__ import annotations

import difflib
import logging
import re

from tests.benchmarks.cloudopsbench.predictor.vocabulary import (
    _FAULT_OBJECT_NAMESPACES,
    _FAULT_OBJECT_NODES,
    _FAULT_OBJECT_SERVICES,
    _ROOT_CAUSES,
)

logger = logging.getLogger(__name__)

_ROOT_CAUSE_BY_NORM: dict[str, str] = {rc.lower(): rc for rc in _ROOT_CAUSES}
_KNOWN_SERVICES_BY_NORM: dict[str, str] = {s.lower(): s for s in _FAULT_OBJECT_SERVICES}
_KNOWN_NODES_BY_NORM: dict[str, str] = {n.lower(): n for n in _FAULT_OBJECT_NODES}
_KNOWN_NAMESPACES_BY_NORM: dict[str, str] = {n.lower(): n for n in _FAULT_OBJECT_NAMESPACES}

# Conservative: only snap a root_cause when the closest canonical token is a
# clear typo/spacing variant. 0.8 catches the observed drift (e.g.
# ``missing_secrectbinding`` → ``missing_secret_binding`` at 0.95,
# ``network_packet_loss`` → ``node_network_packet_loss`` at 0.88) without
# pulling totally unrelated tokens. Note that ratio alone cannot separate
# every legitimate snap from a cross-concept jump — see
# ``_BLOCKED_CONCEPT_PAIRS`` below for the second guard.
_ROOT_CAUSE_SNAP_CUTOFF = 0.8

# Word stems whose canonicals exist in pairs and differ by only a few chars,
# making them susceptible to difflib false-positive snapping. The 11:46 run
# emitted ``readiness_probe_incorrect_timing`` (no canonical for it) which
# scores 0.889 against ``liveness_probe_incorrect_timing`` — above the snap
# cutoff but semantically a different probe type. Raising the global cutoff
# to block this pair would break the legitimate
# ``network_packet_loss`` → ``node_network_packet_loss`` snap (0.884), so we
# express the constraint as an explicit blocklist instead. Extend when other
# concept pairs surface from future runs.
_BLOCKED_CONCEPT_PAIRS: tuple[tuple[str, str], ...] = (("readiness", "liveness"),)


def _crosses_blocked_concept_boundary(predicted_norm: str, snapped: str) -> bool:
    """Refuse a snap that crosses a known concept boundary (readiness↔liveness)."""
    snapped_lower = snapped.lower()
    for a, b in _BLOCKED_CONCEPT_PAIRS:
        # predicted contains stem A AND target contains stem B (and not A) →
        # the snap is rewriting one concept onto a sibling. Symmetric check
        # via the for-loop iterating both orderings.
        if a in predicted_norm and b in snapped_lower and a not in snapped_lower:
            return True
        if b in predicted_norm and a in snapped_lower and b not in snapped_lower:
            return True
    return False


def _snap_root_cause(raw: str) -> str:
    """Snap an LLM-emitted root_cause onto the dataset's closed vocabulary.

    Resolution order: exact (after lower + underscore normalization) →
    ``namespace_*`` admission tokens pass through → closest canonical token by
    difflib ratio above ``_ROOT_CAUSE_SNAP_CUTOFF`` AND not crossing a
    blocked concept boundary. Falls back to the cleaned input when nothing
    is close enough OR the closest match would cross a blocked boundary
    (no regression vs. the pre-snap behavior).
    """
    cleaned = raw.strip()
    if not cleaned:
        return cleaned
    norm = re.sub(r"[\s\-]+", "_", cleaned.lower()).strip("_")
    if norm in _ROOT_CAUSE_BY_NORM:
        return _ROOT_CAUSE_BY_NORM[norm]
    # Namespace-admission faults are an open ``namespace_<reason>`` family the
    # scorer maps to Admission_Fault; keep the normalized form verbatim.
    if norm.startswith("namespace_"):
        return norm
    match = difflib.get_close_matches(
        norm, list(_ROOT_CAUSE_BY_NORM), n=1, cutoff=_ROOT_CAUSE_SNAP_CUTOFF
    )
    if match:
        snapped = _ROOT_CAUSE_BY_NORM[match[0]]
        if _crosses_blocked_concept_boundary(norm, snapped):
            logger.info(
                "[predictor] refused cross-concept snap %r → %r (blocked pair)",
                cleaned,
                snapped,
            )
            return cleaned
        if snapped.lower() != norm:
            logger.info("[predictor] snapped root_cause %r → %r", cleaned, snapped)
        return snapped
    return cleaned


def _snap_fault_object(raw: str) -> str:
    """Normalize a fault_object to the canonical ``<prefix>/<name>`` shape.

    Adds a missing prefix (inferring node/namespace/app from the name) and
    canonicalizes known node/namespace/service tokens. Service names are only
    canonicalized on an exact normalized match — the service list is a known
    subset of the corpus, so fuzzy-snapping here would risk rewriting a correct
    novel service onto a wrong listed one. The scorer already lower-cases both
    sides, so this is scoring-neutral except where it genuinely helps (missing
    prefix, casing of known tokens).
    """
    cleaned = raw.strip()
    if not cleaned:
        return cleaned
    low = cleaned.lower()
    if "/" in low:
        prefix, _, name = low.partition("/")
        prefix, name = prefix.strip(), name.strip()
    else:
        prefix, name = "", low
    if prefix not in {"app", "node", "namespace"}:
        if name in _KNOWN_NODES_BY_NORM:
            prefix = "node"
        elif name in _KNOWN_NAMESPACES_BY_NORM:
            prefix = "namespace"
        else:
            prefix = "app"
    if prefix == "node" and name in _KNOWN_NODES_BY_NORM:
        name = _KNOWN_NODES_BY_NORM[name]
    elif prefix == "namespace" and name in _KNOWN_NAMESPACES_BY_NORM:
        name = _KNOWN_NAMESPACES_BY_NORM[name]
    elif prefix == "app" and name in _KNOWN_SERVICES_BY_NORM:
        name = _KNOWN_SERVICES_BY_NORM[name]
    return f"{prefix}/{name}" if name else cleaned
