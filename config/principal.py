"""Ownership identities for a turn: principal, actor, and their pairing.

Leaf module: safe for any layer to import.

A :class:`Principal` owns credentials, integrations, and the bill. An
:class:`Actor` is the human taking a turn. In an org one principal has many
actors, so the two are separate.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

PrincipalKind = Literal["org", "individual"]


@dataclass(frozen=True)
class Principal:
    """Owner of credentials, integrations, and bill for one install context.

    ``kind="org"`` uses a Clerk (or equivalent) organization id.
    ``kind="individual"`` is reserved for non-organization contexts.
    """

    kind: PrincipalKind
    id: str

    def __post_init__(self) -> None:
        if self.kind not in ("org", "individual"):
            raise ValueError(f"unsupported principal kind: {self.kind!r}")
        principal_id = (self.id or "").strip()
        if not principal_id:
            raise ValueError("principal id must be non-empty")
        object.__setattr__(self, "id", principal_id)

    @classmethod
    def org(cls, org_id: str) -> Principal:
        """Build an organization principal."""
        return cls(kind="org", id=org_id)

    @classmethod
    def individual(cls, individual_id: str) -> Principal:
        """Build an individual principal."""
        return cls(kind="individual", id=individual_id)


@dataclass(frozen=True)
class Actor:
    """The person taking a turn, within whatever principal owns the data."""

    id: str
    display_name: str = ""

    def __post_init__(self) -> None:
        actor_id = (self.id or "").strip()
        if not actor_id:
            raise ValueError("actor id must be non-empty")
        object.__setattr__(self, "id", actor_id)


@dataclass(frozen=True)
class StorageScope:
    """Who owns the data and who is reading it for one turn."""

    principal: Principal
    actor: Actor


__all__ = [
    "Actor",
    "Principal",
    "PrincipalKind",
    "StorageScope",
]
