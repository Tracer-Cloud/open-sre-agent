"""Turn-wide assembly: the decisions one turn runs on.

Assembled once at the top of ``run_turn`` and read by the action, gather, and
answer phases so they cannot disagree about what this turn knows. It composes the
frozen :class:`TurnSnapshot` (the read view of session state at turn start) with
the turn's resolved-integration decision.

The snapshot answers "what did the session look like at turn start?"; the plan
answers "what is this turn running on?". ``build_turn_plan`` owns the assembly:
it resolves integrations once and composes them into the snapshot. Tool lists and
prompts stay built by their phases (action tools need surface context; gather
tools depend on message-time GitHub scope), each reading ``resolved_integrations``
here so there is one source.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Any

from core.agent_harness.ports import SessionState
from core.agent_harness.session.integration_resolution import (
    has_resolved_integrations,
    resolve_and_cache_integrations,
)
from core.agent_harness.turns.turn_snapshot import TurnSnapshot
from infrastructure.harness_providers import enrich_resolved_with_repo_scopes

_MAX_KNOWN_REPOSITORIES_PER_VENDOR = 20


@dataclass(frozen=True)
class TurnPlan:
    """Everything one turn runs on, assembled once at ``run_turn``."""

    snapshot: TurnSnapshot

    @property
    def text(self) -> str:
        """Raw user input text for this turn."""
        return self.snapshot.text

    @property
    def resolved_integrations(self) -> dict[str, Any]:
        """The turn's single resolved-integration view (frozen on the snapshot)."""
        return self.snapshot.resolved_integrations


def build_turn_plan(snapshot: TurnSnapshot, session: SessionState) -> TurnPlan:
    """Assemble the turn plan: resolve integrations once, then compose the snapshot.

    Resolution runs only when the snapshot has not already been populated (a
    runtime-request source can pre-fill it), so the plan is the single place that
    decides what this turn knows about connected integrations.

    An empty result (``{}`` — no integrations configured) is a valid resolved
    view; downstream phases read it from the plan rather than re-checking, so the
    resolve-once contract holds even in that case (``resolve_and_cache`` also
    caches, so a repeat call would be a no-op regardless).

    Metadata-only maps (underscore keys such as ``_gateway_chat_id``) are not a
    resolved view — they must still trigger a real resolve.
    """
    if not has_resolved_integrations(snapshot.resolved_integrations):
        snapshot = replace(snapshot, resolved_integrations=resolve_and_cache_integrations(session))

    repository_keys: dict[tuple[str, tuple[str, ...]], str] = {}

    def _set_active_scope(vendor: str, scope: tuple[str, ...] | None) -> None:
        active_scopes = dict(session.vcs_repo_scopes)
        if scope is None:
            active_scopes.pop(vendor, None)
        else:
            active_scopes[vendor] = scope
        session.vcs_repo_scopes = active_scopes

        active_repositories = dict(session.active_vcs_repositories)
        repository = repository_keys.get((vendor, scope)) if scope is not None else None
        if repository is None:
            active_repositories.pop(vendor, None)
        else:
            active_repositories[vendor] = repository
        session.active_vcs_repositories = active_repositories

    def _remember_scope(vendor: str, repository: str, scope: tuple[str, ...]) -> None:
        repository_keys[(vendor, scope)] = repository
        known_by_vendor = {
            name: dict(scopes) for name, scopes in session.known_vcs_repo_scopes.items()
        }
        known = known_by_vendor.setdefault(vendor, {})
        # Re-inserting moves a reused repository to the recent end without
        # creating a duplicate. Bound the collection for long-running gateways.
        known.pop(repository, None)
        known[repository] = scope
        while len(known) > _MAX_KNOWN_REPOSITORIES_PER_VENDOR:
            known.pop(next(iter(known)))
        session.known_vcs_repo_scopes = known_by_vendor

    enriched = enrich_resolved_with_repo_scopes(
        resolved=snapshot.resolved_integrations,
        message=snapshot.text,
        conversation_messages=snapshot.conversation_messages,
        env=None,
        cwd=snapshot.working_directory,
        cached_scopes=session.vcs_repo_scopes,
        set_cached_scope=_set_active_scope,
        remember_scope=_remember_scope,
    )
    snapshot = replace(
        snapshot,
        resolved_integrations=enriched,
        active_vcs_repositories=dict(session.active_vcs_repositories),
        known_vcs_repositories={
            vendor: tuple(scopes) for vendor, scopes in session.known_vcs_repo_scopes.items()
        },
    )
    return TurnPlan(snapshot=snapshot)


__all__ = ["TurnPlan", "build_turn_plan"]
