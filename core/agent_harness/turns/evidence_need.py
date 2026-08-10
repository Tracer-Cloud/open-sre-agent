"""Evidence tier from action-agent handoff tags (not user-text keywords).

The action planner emits structured tags such as ``evidence_kind:metric_read``.
This module turns those tags + connected integrations into an
:class:`EvidenceNeed` so the orchestrator can skip empty gather and append a CTA.

After an L1 gather, :func:`reclassify_evidence_need_after_gather` may flip the
need to ``L0_degraded`` when preferred-source failures look like config/auth
(not HogQL / empty-result noise).

Do not scan user prose here — see workspace rule no-keyword-intent-routing.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass, replace
from enum import StrEnum
from typing import TYPE_CHECKING, Any

from core.agent_harness.turns.evidence_kind import EvidenceKind, policy_for
from core.agent_harness.turns.handoff_keys import HandoffField, HandoffTag
from core.agent_harness.turns.handoff_tag_parse import first_tag_token

if TYPE_CHECKING:
    from core.agent_harness.turns.assistant_handoff import AssistantHandoff


class EvidenceTier(StrEnum):
    """How much live evidence the turn can actually reach."""

    L0_DEGRADED = "L0_degraded"
    L1 = "L1"
    L0 = "L0"


class EvidenceDegradeCause(StrEnum):
    """Why a metric/read turn landed on ``L0_degraded``."""

    MISSING_SOURCE = "missing_source"
    CONFIG_FAILURE = "config_failure"


PreferredSourcesForKind = Callable[[EvidenceKind], tuple[str, ...]]
# Renders the surface command that connects ``service_id`` (core knows no slash syntax).
SetupCommandForSource = Callable[[str], str]

# Auth/config failure signatures in gather observation text (preferred-source
# scoped). Application query noise — HogQL syntax, empty results — must not
# match. Same spirit as source_circuit_breaker connectivity markers.
_CONFIG_FAILURE_MARKERS = (
    "not configured",
    "unauthorized",
    "authentication failed",
    "authentication error",
    "invalid api key",
    "invalid token",
    "missing credentials",
    "missing api key",
    "forbidden",
    "401",
    "403",
    '"available": false',
    '"available":false',
)


@dataclass(frozen=True, slots=True)
class EvidenceNeed:
    """What live evidence this turn needs and whether it is available."""

    kind: EvidenceKind
    preferred_sources: tuple[str, ...]
    connected: tuple[str, ...]
    missing: tuple[str, ...]
    tier: EvidenceTier
    required_for_authoritative: bool
    # Set when ``tier`` is ``L0_degraded`` (missing preferred source, or
    # connected source failed auth/config after gather).
    degrade_cause: EvidenceDegradeCause | None = None


def evidence_kind_from_handoffs(handoff_contents: Sequence[str]) -> EvidenceKind | None:
    """Return the first ``evidence_kind`` tag from action handoffs, if any.

    Accepts ``evidence_kind:metric_read`` or ``evidence_kind=metric_read`` (schema
    docs use ``=``; legacy content tags use ``:``), a tag followed by planner
    prose, or a tag buried mid-content. Only the first token after the separator
    is the kind. Parsing is plain string splits — not regex, not user-text scan.
    """
    for raw in handoff_contents:
        token = first_tag_token(raw, HandoffField.EVIDENCE_KIND)
        if token is None:
            continue
        try:
            return EvidenceKind(token)
        except ValueError:
            continue
    return None


def _connected_names(resolved_integrations: dict[str, Any] | None) -> frozenset[str]:
    if not resolved_integrations:
        return frozenset()
    # Truthiness, not ``is not None``: a name whose config resolved to ``{}``
    # is registered but has nothing to query, and must not read as live.
    return frozenset(name for name, value in resolved_integrations.items() if value)


def classify_evidence_need(
    *,
    handoff_contents: Sequence[str] = (),
    handoffs: Sequence[AssistantHandoff] = (),
    resolved_integrations: dict[str, Any] | None = None,
    preferred_sources_for: PreferredSourcesForKind | None = None,
    kind: EvidenceKind | None = None,
) -> EvidenceNeed:
    """Return the evidence tier from typed handoffs (or legacy tag strings).

    Prefer ``handoffs`` (:class:`AssistantHandoff` fields). Fall back to parsing
    ``handoff_contents`` tags only when no structured kind is present. Never
    infers intent from user text.

    Kind-specific behavior comes from :func:`policy_for` — do not add
    ``if kind is …`` branches here when introducing a new evidence kind.
    """
    resolved_kind = kind
    if resolved_kind is None and handoffs:
        from core.agent_harness.turns.assistant_handoff import (
            evidence_kind_from_assistant_handoffs,
        )

        resolved_kind = evidence_kind_from_assistant_handoffs(handoffs)
    if resolved_kind is None:
        resolved_kind = evidence_kind_from_handoffs(handoff_contents)
    resolved_kind = resolved_kind or EvidenceKind.OTHER
    connected = _connected_names(resolved_integrations)
    policy = policy_for(resolved_kind)
    if policy.ignore_preferred_sources:
        preferred: tuple[str, ...] = ()
    else:
        preferred = (
            preferred_sources_for(resolved_kind) if preferred_sources_for is not None else ()
        )

    if policy.requires_authoritative_source and preferred:
        present = tuple(name for name in preferred if name in connected)
        absent = tuple(name for name in preferred if name not in connected)
        if absent:
            return EvidenceNeed(
                kind=resolved_kind,
                preferred_sources=preferred,
                connected=present,
                missing=absent,
                tier=EvidenceTier.L0_DEGRADED,
                required_for_authoritative=True,
                degrade_cause=EvidenceDegradeCause.MISSING_SOURCE,
            )
        return EvidenceNeed(
            kind=resolved_kind,
            preferred_sources=preferred,
            connected=present,
            missing=(),
            tier=EvidenceTier.L1,
            required_for_authoritative=True,
        )

    return EvidenceNeed(
        kind=resolved_kind,
        preferred_sources=preferred,
        connected=tuple(sorted(connected)),
        missing=(),
        tier=EvidenceTier.L0 if not connected else EvidenceTier.L1,
        required_for_authoritative=False,
    )


def _source_mentioned(observation_lower: str, source: str) -> bool:
    """True when the observation names this preferred source (or a close alias)."""
    needle = source.lower().strip()
    if not needle:
        return False
    if needle in observation_lower:
        return True
    # ``posthog_mcp`` observations often say ``posthog`` / ``PostHog MCP``.
    base = needle.removesuffix("_mcp")
    return bool(base) and base in observation_lower


def _window_has_config_failure(window: str) -> bool:
    return any(marker in window for marker in _CONFIG_FAILURE_MARKERS)


def _preferred_sources_with_config_failure(
    observation: str,
    preferred: tuple[str, ...],
) -> tuple[str, ...]:
    """Return preferred source ids whose local observation window looks like auth/config failure."""
    lowered = observation.lower()
    failed: list[str] = []
    for source in preferred:
        if not _source_mentioned(lowered, source):
            continue
        # Prefer a window around the source name so unrelated 401s elsewhere
        # do not poison this preferred vendor.
        idx = lowered.find(source.lower())
        if idx < 0:
            base = source.lower().removesuffix("_mcp")
            idx = lowered.find(base) if base else -1
        if idx < 0:
            continue
        start = max(0, idx - 160)
        end = min(len(lowered), idx + len(source) + 240)
        window = lowered[start:end]
        if _window_has_config_failure(window):
            failed.append(source)
    return tuple(failed)


def reclassify_evidence_need_after_gather(
    need: EvidenceNeed,
    observation: str | None,
) -> EvidenceNeed:
    """Flip L1 → L0_degraded when gather shows preferred-source config/auth failure.

    HogQL / empty-result failures stay L1 (honest answer, no UpgradeCTA).
    Missing-source L0 is decided before gather and is left unchanged.
    """
    if (
        need.tier != EvidenceTier.L1
        or not need.required_for_authoritative
        or not need.connected
        or not observation
        or not observation.strip()
    ):
        return need

    preferred = need.preferred_sources or need.connected
    failed = _preferred_sources_with_config_failure(observation, preferred)
    if not failed:
        return need

    return replace(
        need,
        tier=EvidenceTier.L0_DEGRADED,
        connected=(),
        missing=failed,
        degrade_cause=EvidenceDegradeCause.CONFIG_FAILURE,
    )


def should_skip_gather(need: EvidenceNeed) -> bool:
    """True when gather would only thrash empty discovery for this need.

    Config-failure L0 is discovered *after* gather — never skip gather for it.
    """
    return (
        need.tier == EvidenceTier.L0_DEGRADED
        and need.required_for_authoritative
        and need.degrade_cause != EvidenceDegradeCause.CONFIG_FAILURE
    )


def should_suppress_investigation_offer(need: EvidenceNeed) -> bool:
    """True when Want-me-to investigate would be the wrong closer.

    Driven by :class:`~core.agent_harness.turns.evidence_kind.EvidenceKindPolicy`
    (e.g. metric/read → query/setup next, not RCA).
    """
    return policy_for(need.kind).suppress_investigation_offer


def format_upgrade_cta(
    need: EvidenceNeed,
    *,
    setup_command_for: SetupCommandForSource,
) -> str | None:
    """One-paragraph upgrade CTA, or ``None`` when the turn is already live.

    ``setup_command_for`` renders the surface's connect command; core does not
    know slash syntax. Every missing source is named — mentioning only the
    first leaves the user degraded after connecting it.
    """
    if need.tier != EvidenceTier.L0_DEGRADED or not need.missing:
        return None
    names = ", ".join(f"`{name}`" for name in need.missing)
    commands = "\n".join(f"- `{setup_command_for(name)}`" for name in need.missing)
    subject = "that source" if len(need.missing) == 1 else "those sources"
    if need.degrade_cause == EvidenceDegradeCause.CONFIG_FAILURE:
        verb = "is" if len(need.missing) == 1 else "are"
        return (
            f"{names} {verb} connected in this session, but the live query failed "
            f"because of credentials or configuration — I can't return an "
            f"authoritative number from {subject}.\n\n"
            "Reconnect or fix the integration, then ask again:\n"
            f"{commands}"
        )
    return (
        f"I don't have {names} connected in this session, so I can't return a "
        f"live number from {subject}.\n\n"
        "Connect it, then ask again for the authoritative count:\n"
        f"{commands}"
    )


def cta_offered_key(need: EvidenceNeed) -> str | None:
    """Session dedupe key for an offered CTA, or ``None`` when none applies.

    Keyed on the whole missing set: connecting one of two sources changes the
    ask, so the narrower CTA must be allowed to appear. Config-failure CTAs
    share the same key family so a missing-source offer and a reconnect offer
    for the same id still dedupe while armed.
    """
    if need.tier != EvidenceTier.L0_DEGRADED or not need.missing:
        return None
    return "cta:" + ",".join(need.missing)


def handoff_tag_for(need: EvidenceNeed) -> str | None:
    """Synthetic handoff tag so the answer path gets L0 guidance.

    Prefix-matched by ``build_handoff_guidance_block``. Missing-source suffix is
    the service ids; config-failure suffix is ``config:<ids>``.
    """
    if need.tier != EvidenceTier.L0_DEGRADED or not need.missing:
        return None
    missing = ",".join(need.missing)
    if need.degrade_cause == EvidenceDegradeCause.CONFIG_FAILURE:
        return f"{HandoffTag.EVIDENCE_TIER}:{EvidenceTier.L0_DEGRADED}:config:{missing}"
    return f"{HandoffTag.EVIDENCE_TIER}:{EvidenceTier.L0_DEGRADED}:{missing}"


__all__ = [
    "EvidenceDegradeCause",
    "EvidenceKind",
    "EvidenceNeed",
    "EvidenceTier",
    "PreferredSourcesForKind",
    "SetupCommandForSource",
    "classify_evidence_need",
    "cta_offered_key",
    "evidence_kind_from_handoffs",
    "format_upgrade_cta",
    "handoff_tag_for",
    "reclassify_evidence_need_after_gather",
    "should_skip_gather",
    "should_suppress_investigation_offer",
]
