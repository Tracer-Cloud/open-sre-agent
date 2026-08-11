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
from core.agent_harness.turns.gather_observation import GatheredEvidence

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
        (
            f"Tool: {_FAKE_ANALYTICS} execute-sql\n"
            "Result: {'source': 'fake_analytics', 'available': False, "
            "'error': '401 Unauthorized — invalid api key'}"
        ),
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
            (
                f"Tool: {_FAKE_ANALYTICS}\n"
                "Result: {'source': 'fake_analytics', 'available': False, "
                "'error': '401 Unauthorized'}"
            ),
        )
        is need
    )


def test_successful_result_values_are_not_read_as_config_failures() -> None:
    """Ordinary result values must not look like tool_unavailable.

    Greptile P1: a successful preferred-source result that *mentions*
    ``unauthorized access``, ``permission denied``, or nested
    ``"available": false`` as data must stay L1 — never discard it and tell
    the user to reconnect.
    """
    from core.agent_harness.turns.evidence_need import _preferred_sources_with_config_failure

    sources = ("posthog_mcp",)

    # A successful query whose count happens to be 401.
    counted = 'Tool: posthog_mcp query\nResult: {"results": [["Windows", 401]], "status": "ok"}'
    assert _preferred_sources_with_config_failure(sources, observation=counted) == ()

    # Prose / names that used to trip the old phrase list.
    for observation in (
        'Tool: posthog_mcp\nResult: {"rows": [["forbidden-city-campaign", 12]]}',
        'Tool: posthog_mcp\nResult: {"rows": [["unauthorized-users-cohort", 87]]}',
        'Tool: posthog_mcp\nResult: {"rows": [["unauthorized access", 3]]}',
        'Tool: posthog_mcp\nResult: {"rows": [["permission denied events", 9]]}',
        'Tool: posthog_mcp\nResult: {"rows": [["access denied page", 1]]}',
        # Nested available:false without top-level error (feature-flag shape).
        'Tool: posthog_mcp\nResult: {"flags": {"unauthorized_banner": {"available": false}}}',
        # available:false without error — not the typed envelope.
        'Tool: posthog_mcp\nResult: {"source": "posthog_mcp", "available": false}',
        # Free-text auth wording without an envelope — fail closed (no degrade).
        "Tool: posthog_mcp\nResult: error: 401 Unauthorized, invalid api key",
        "Tool: posthog_mcp\nResult: permission denied",
    ):
        assert _preferred_sources_with_config_failure(sources, observation=observation) == (), (
            observation
        )


def test_a_real_auth_failure_is_still_detected() -> None:
    """Typed tool_unavailable envelopes still flip preferred sources."""
    from core.agent_harness.turns.evidence_need import _preferred_sources_with_config_failure

    sources = ("posthog_mcp",)
    for observation in (
        "Tool: posthog_mcp execute-sql\n"
        "Result: {'source': 'posthog_mcp', 'available': False, "
        "'error': '401 Unauthorized — invalid api key'}",
        "Tool: posthog_mcp\n"
        'Result: {"source": "posthog", "available": false, "error": "not configured"}',
        # Envelope without source — Tool: line supplies the vendor.
        "Tool: posthog_mcp list_tools\n"
        "Result: {'available': False, 'error': 'authentication failed'}",
        # JSON envelope embedded without Tool:/Result: wrapper.
        '{"source": "posthog_mcp", "available": false, "error": "invalid api key"}',
    ):
        assert (
            _preferred_sources_with_config_failure(sources, observation=observation) == sources
        ), observation


def test_structured_tool_results_flip_without_scraping_observation() -> None:
    """GatheredEvidence.tool_results is the preferred reclassify path."""
    from core.agent_harness.turns.gather_observation import GatheredEvidence
    from core.tool_framework.utils.tool_availability import tool_unavailable

    need = _l1_metric_need()
    # Observation looks like a happy path; structured payload says otherwise.
    gathered = GatheredEvidence(
        observation=f"Tool: {_FAKE_ANALYTICS}\nResult: windows_users=42",
        tool_results=(
            (
                f"{_FAKE_ANALYTICS} execute-sql",
                tool_unavailable(_FAKE_ANALYTICS, "401 Unauthorized"),
            ),
        ),
    )
    flipped = reclassify_evidence_need_after_gather(need, gathered)
    assert flipped.tier == EvidenceTier.L0_DEGRADED
    assert flipped.degrade_cause == EvidenceDegradeCause.CONFIG_FAILURE
    assert flipped.missing == (_FAKE_ANALYTICS,)


def test_truncated_gather_degrades_but_a_finished_empty_gather_does_not() -> None:
    """The cut-off flag decides the tier, not the observation text.

    A gather that ran out of iterations never reached the answer, so the turn
    must proceed without that source. A gather that finished and found nothing
    is an honest empty answer and stays L1.
    """
    # Arrange — identical evidence, differing only in how the loop ended.
    need = _l1_metric_need()
    assert need.tier == EvidenceTier.L1
    observation = f"Tool: {_FAKE_ANALYTICS}\nResult: event definitions listed"
    cut_off = GatheredEvidence(observation=observation, truncated=True)
    finished = GatheredEvidence(observation=observation, truncated=False)

    # Act.
    after_cut_off = reclassify_evidence_need_after_gather(need, cut_off)
    after_finished = reclassify_evidence_need_after_gather(need, finished)

    # Assert — only the truncated run degrades.
    assert after_cut_off.tier == EvidenceTier.L0_DEGRADED
    assert after_cut_off.degrade_cause == EvidenceDegradeCause.GATHER_TRUNCATED
    assert after_cut_off.missing == (_FAKE_ANALYTICS,)
    assert after_finished is need

    # The answer path gets plain L0 guidance ("answer without live data"),
    # not the config-failure reconnect prompt.
    tag = handoff_tag_for(after_cut_off)
    assert tag == f"evidence_tier:L0_degraded:{_FAKE_ANALYTICS}"
    assert ":config:" not in tag
