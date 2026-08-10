"""EvidenceNeed: tier from action handoff tags when preferred sources are missing."""

from __future__ import annotations

from core.agent_harness.turns.evidence_need import (
    EvidenceDegradeCause,
    EvidenceKind,
    EvidenceTier,
    classify_evidence_need,
    cta_offered_key,
    evidence_kind_from_handoffs,
    format_upgrade_cta,
    handoff_tag_for,
    reclassify_evidence_need_after_gather,
    should_skip_gather,
    should_suppress_investigation_offer,
)

_FAKE_ANALYTICS = "analytics_source"


def _prefer_fake_analytics(kind: EvidenceKind) -> tuple[str, ...]:
    return (_FAKE_ANALYTICS,) if kind == "metric_read" else ()


def test_metric_kind_from_handoff_degrades_when_preferred_source_missing() -> None:
    need = classify_evidence_need(
        handoff_contents=("evidence_kind:metric_read", "Look up Windows users."),
        resolved_integrations={},
        preferred_sources_for=_prefer_fake_analytics,
    )

    assert need.kind == "metric_read"
    assert need.preferred_sources == (_FAKE_ANALYTICS,)
    assert need.missing == (_FAKE_ANALYTICS,)
    assert need.tier == EvidenceTier.L0_DEGRADED
    assert need.degrade_cause == EvidenceDegradeCause.MISSING_SOURCE
    assert should_skip_gather(need) is True
    assert should_suppress_investigation_offer(need) is True


def test_metric_kind_is_live_when_preferred_source_connected() -> None:
    need = classify_evidence_need(
        handoff_contents=("evidence_kind:metric_read",),
        resolved_integrations={_FAKE_ANALYTICS: {"configured": True}},
        preferred_sources_for=_prefer_fake_analytics,
    )

    assert need.tier == EvidenceTier.L1
    assert need.connected == (_FAKE_ANALYTICS,)
    assert need.missing == ()
    assert should_skip_gather(need) is False


def test_no_handoff_kind_does_not_infer_from_absent_tags() -> None:
    """Without evidence_kind tags, core must not invent metric_read."""
    need = classify_evidence_need(
        handoff_contents=("Look up the Windows user count.",),
        resolved_integrations={},
        preferred_sources_for=_prefer_fake_analytics,
    )

    assert need.kind == "other"
    assert need.tier != EvidenceTier.L0_DEGRADED
    assert should_skip_gather(need) is False


def test_incident_kind_from_handoff_is_not_metric_degradation() -> None:
    need = classify_evidence_need(
        handoff_contents=("evidence_kind:incident", "checkout 502s"),
        resolved_integrations={},
        preferred_sources_for=_prefer_fake_analytics,
    )

    assert need.kind == "incident"
    assert need.tier != EvidenceTier.L0_DEGRADED
    assert should_skip_gather(need) is False


def test_upgrade_cta_names_setup_command_for_missing_source() -> None:
    need = classify_evidence_need(
        handoff_contents=("evidence_kind:metric_read",),
        resolved_integrations={},
        preferred_sources_for=_prefer_fake_analytics,
    )
    cta = format_upgrade_cta(need, setup_command_for=lambda name: f"/integrations setup {name}")

    assert cta is not None
    assert _FAKE_ANALYTICS in cta
    assert f"/integrations setup {_FAKE_ANALYTICS}" in cta
    assert "can't return a live number" in cta
    assert "ask again" in cta.lower()


def test_evidence_kind_from_handoffs_parses_tag() -> None:
    assert (
        evidence_kind_from_handoffs(("chat:greeting", "evidence_kind:metric_read")) == "metric_read"
    )
    assert evidence_kind_from_handoffs(("chat:greeting",)) is None


def test_evidence_kind_from_handoffs_accepts_tag_then_prose() -> None:
    """Planners often emit ``evidence_kind:metric_read - …``; only the token is the kind."""
    assert (
        evidence_kind_from_handoffs(
            ("evidence_kind:metric_read - PostHog query: Windows users last 7 days.",)
        )
        == "metric_read"
    )


def test_an_unconfigured_integration_does_not_count_as_connected() -> None:
    """A name present with an empty config is not a usable live source.

    ``resolve_and_cache_integrations`` returns ``{name: config}``; a name whose
    config resolved to nothing is registered but unusable. Treating it as
    connected reports tier L1 and skips the upgrade CTA, so the turn silently
    claims live data it cannot query.
    """
    # Arrange: posthog is present but has no usable config.
    need = classify_evidence_need(
        handoff_contents=("evidence_kind:metric_read",),
        resolved_integrations={"posthog": {}},
        preferred_sources_for=lambda _kind: ("posthog",),
    )

    # Assert.
    assert need.missing == ("posthog",)
    assert need.tier == EvidenceTier.L0_DEGRADED


def test_the_cta_names_every_missing_source_not_just_the_first() -> None:
    """Two missing sources must both appear, or the user connects one and stays degraded."""
    # Arrange.
    need = classify_evidence_need(
        handoff_contents=("evidence_kind:metric_read",),
        resolved_integrations={},
        preferred_sources_for=lambda _kind: ("posthog", "amplitude"),
    )

    # Act.
    cta = format_upgrade_cta(need, setup_command_for=lambda name: f"/integrations setup {name}")

    # Assert.
    assert need.missing == ("posthog", "amplitude")
    assert cta is not None
    assert "posthog" in cta
    assert "amplitude" in cta


def test_the_dedupe_key_distinguishes_different_missing_sets() -> None:
    """One key per missing set, else connecting one source re-offers the same key."""

    # Arrange.
    def _need(*missing: str) -> object:
        return classify_evidence_need(
            handoff_contents=("evidence_kind:metric_read",),
            resolved_integrations={},
            preferred_sources_for=lambda _kind: missing,
        )

    # Act / Assert.
    assert cta_offered_key(_need("posthog")) != cta_offered_key(_need("posthog", "amplitude"))


def test_evidence_tier_and_kind_are_string_enums() -> None:
    """Closed vocabularies are enums, so a typo cannot pass as a tier or kind."""
    # Arrange / Act / Assert.
    assert isinstance(EvidenceTier.L0_DEGRADED, str)
    assert EvidenceTier("L0_degraded") is EvidenceTier.L0_DEGRADED
    assert EvidenceKind("metric_read") is EvidenceKind.METRIC_READ
    assert EvidenceDegradeCause("config_failure") is EvidenceDegradeCause.CONFIG_FAILURE


def _l1_metric_need():
    return classify_evidence_need(
        handoff_contents=("evidence_kind:metric_read",),
        resolved_integrations={_FAKE_ANALYTICS: {"configured": True}},
        preferred_sources_for=_prefer_fake_analytics,
    )


def test_reclassify_after_gather_flips_on_auth_failure() -> None:
    need = _l1_metric_need()
    assert need.tier == EvidenceTier.L1

    flipped = reclassify_evidence_need_after_gather(
        need,
        f"Tool: {_FAKE_ANALYTICS}\nResult: error 401 unauthorized — invalid api key",
    )

    assert flipped.tier == EvidenceTier.L0_DEGRADED
    assert flipped.degrade_cause == EvidenceDegradeCause.CONFIG_FAILURE
    assert flipped.missing == (_FAKE_ANALYTICS,)
    assert should_skip_gather(flipped) is False  # config L0 is post-gather
    tag = handoff_tag_for(flipped)
    assert tag is not None
    assert tag.startswith("evidence_tier:L0_degraded:config:")
    cta = format_upgrade_cta(flipped, setup_command_for=lambda n: f"/integrations setup {n}")
    assert cta is not None
    assert "credentials or configuration" in cta
    assert f"/integrations setup {_FAKE_ANALYTICS}" in cta


def test_reclassify_after_gather_ignores_hogql_and_empty_results() -> None:
    need = _l1_metric_need()
    for observation in (
        f"Tool: {_FAKE_ANALYTICS}\nResult: HogQL syntax error near SELECT",
        f"Tool: {_FAKE_ANALYTICS}\nResult: no events in window (empty result)",
        f"Tool: {_FAKE_ANALYTICS}\nResult: windows_users=42",
        None,
        "",
    ):
        assert reclassify_evidence_need_after_gather(need, observation) is need


def test_reclassify_leaves_missing_source_l0_unchanged() -> None:
    need = classify_evidence_need(
        handoff_contents=("evidence_kind:metric_read",),
        resolved_integrations={},
        preferred_sources_for=_prefer_fake_analytics,
    )
    assert need.degrade_cause == EvidenceDegradeCause.MISSING_SOURCE
    assert (
        reclassify_evidence_need_after_gather(
            need,
            f"{_FAKE_ANALYTICS} 401 unauthorized",
        )
        is need
    )
