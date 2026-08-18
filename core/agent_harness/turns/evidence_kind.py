"""What kind of evidence a turn is asking for — vocabulary + per-kind policy.

A leaf so ``evidence_need`` (which classifies) and ``assistant_handoff`` (which
parses the planner's tag) can both name the vocabulary without importing each
other. Holding it in either of them makes the pair a cycle.

Open/closed: add a kind by extending the enum **and** registering its
:class:`EvidenceKindPolicy` here (or via :func:`register_evidence_kind_policy`).
Do not grow ``if kind is …`` branches inside ``classify_evidence_need``.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from enum import StrEnum


class EvidenceKind(StrEnum):
    """What the action planner said this turn is asking for."""

    METRIC_READ = "metric_read"
    INCIDENT = "incident"
    SETUP = "setup"
    OTHER = "other"


# Stable tuple for tool JSON Schema ``enum`` — always derived from the StrEnum.
EVIDENCE_KIND_VALUES: tuple[str, ...] = tuple(kind.value for kind in EvidenceKind)


@dataclass(frozen=True, slots=True)
class EvidenceKindPolicy:
    """Per-kind classify / Want-me-to behavior (data, not an if-chain).

    ``requires_authoritative_source``: when preferred integrations are registered
    for this kind and none are connected → ``L0_degraded`` + skip empty gather.
    ``suppress_investigation_offer``: do not close with Want-me-to investigate.
    ``ignore_preferred_sources``: never attach preferred ids on the need
    (incident handoffs are not metric-source asks).
    """

    requires_authoritative_source: bool = False
    suppress_investigation_offer: bool = False
    ignore_preferred_sources: bool = False


_DEFAULT_POLICY = EvidenceKindPolicy()

# Kind → policy. New kinds get a row here (or via register); classify stays closed.
_KIND_POLICIES: dict[EvidenceKind, EvidenceKindPolicy] = {
    EvidenceKind.METRIC_READ: EvidenceKindPolicy(
        requires_authoritative_source=True,
        suppress_investigation_offer=True,
    ),
    EvidenceKind.INCIDENT: EvidenceKindPolicy(
        ignore_preferred_sources=True,
    ),
}


def policy_for(kind: EvidenceKind) -> EvidenceKindPolicy:
    """Return the policy for ``kind``, or the neutral default."""
    return _KIND_POLICIES.get(kind, _DEFAULT_POLICY)


def register_evidence_kind_policy(kind: EvidenceKind, policy: EvidenceKindPolicy) -> None:
    """Install or replace policy for ``kind`` (tests / future kind extensions)."""
    _KIND_POLICIES[kind] = policy


@contextmanager
def temporary_evidence_kind_policy(
    kind: EvidenceKind, policy: EvidenceKindPolicy
) -> Iterator[None]:
    """Scoped policy override for tests — restores the previous row on exit."""
    previous = _KIND_POLICIES.get(kind)
    had_previous = kind in _KIND_POLICIES
    register_evidence_kind_policy(kind, policy)
    try:
        yield
    finally:
        if had_previous and previous is not None:
            _KIND_POLICIES[kind] = previous
        else:
            _KIND_POLICIES.pop(kind, None)


__all__ = [
    "EVIDENCE_KIND_VALUES",
    "EvidenceKind",
    "EvidenceKindPolicy",
    "policy_for",
    "register_evidence_kind_policy",
    "temporary_evidence_kind_policy",
]
