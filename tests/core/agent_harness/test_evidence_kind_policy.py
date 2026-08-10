"""EvidenceKindPolicy: open/closed — new kind behavior via policy rows, not classify edits."""

from __future__ import annotations

from core.agent_harness.turns.evidence_kind import (
    EVIDENCE_KIND_VALUES,
    EvidenceKind,
    EvidenceKindPolicy,
    policy_for,
    temporary_evidence_kind_policy,
)
from core.agent_harness.turns.evidence_need import (
    EvidenceTier,
    classify_evidence_need,
    should_suppress_investigation_offer,
)


def test_metric_policy_is_authoritative_and_suppresses_investigate() -> None:
    policy = policy_for(EvidenceKind.METRIC_READ)
    assert policy.requires_authoritative_source is True
    assert policy.suppress_investigation_offer is True


def test_incident_policy_ignores_preferred_sources() -> None:
    def _prefer_incident(kind: str) -> tuple[str, ...]:
        return tuple(source for source in ("posthog_mcp",) if kind == "incident")

    need = classify_evidence_need(
        handoff_contents=("evidence_kind:incident",),
        resolved_integrations={},
        preferred_sources_for=_prefer_incident,
    )
    assert need.kind == "incident"
    assert need.preferred_sources == ()
    assert need.tier != EvidenceTier.L0_DEGRADED


def test_setup_kind_can_opt_into_authoritative_degradation_via_policy() -> None:
    """Adding authoritative behavior for an existing kind is a policy row, not a classify branch."""

    def _prefer_setup(kind: EvidenceKind) -> tuple[str, ...]:
        return tuple(source for source in ("vault_mcp",) if kind is EvidenceKind.SETUP)

    with temporary_evidence_kind_policy(
        EvidenceKind.SETUP,
        EvidenceKindPolicy(
            requires_authoritative_source=True,
            suppress_investigation_offer=True,
        ),
    ):
        need = classify_evidence_need(
            handoff_contents=("evidence_kind:setup",),
            resolved_integrations={},
            preferred_sources_for=_prefer_setup,
        )
        assert need.tier == EvidenceTier.L0_DEGRADED
        assert need.missing == ("vault_mcp",)
        assert should_suppress_investigation_offer(need) is True

    # Restored: SETUP is not authoritative by default.
    restored = classify_evidence_need(
        handoff_contents=("evidence_kind:setup",),
        resolved_integrations={},
        preferred_sources_for=_prefer_setup,
    )
    assert restored.tier != EvidenceTier.L0_DEGRADED
    assert should_suppress_investigation_offer(restored) is False


def test_evidence_kind_values_cover_every_enum_member() -> None:
    assert set(EVIDENCE_KIND_VALUES) == {kind.value for kind in EvidenceKind}
