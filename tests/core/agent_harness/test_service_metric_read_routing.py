"""A metric question that names its own system does not answer from the analytics vendor.

`what's the current error rate on github?` used to answer from PostHog: the
planner tagged it ``metric_read``, and the only registrant for that kind is
``posthog_mcp``, so the lookup — keyed on the kind alone — never saw that the
question named GitHub. PostHog then could not form the query, and the turn
closed with draft HogQL full of ``<github_error_event>`` placeholders plus
``/integrations setup posthog_mcp``.

``service_metric_read`` is where such a question belongs. It registers no
authoritative source, so the need falls through to an ordinary gather and the
action phase reads the system the user named.
"""

from __future__ import annotations

from core.agent_harness.turns.assistant_handoff import AssistantHandoff
from core.agent_harness.turns.evidence_kind import EvidenceKind, policy_for
from core.agent_harness.turns.evidence_need import (
    EvidenceNeed,
    EvidenceTier,
    classify_evidence_need,
    should_suppress_investigation_offer,
)
from core.agent_harness.turns.handoff_keys import HandoffField
from core.agent_harness.turns.metric_query_floor import apply_unformed_metric_floor


def _analytics_owns_metric_read(kind: EvidenceKind) -> tuple[str, ...]:
    """The registration that exists in production: one vendor, one kind."""
    return ("posthog_mcp",) if kind is EvidenceKind.METRIC_READ else ()


def test_a_named_system_metric_does_not_demand_the_analytics_vendor() -> None:
    # Arrange: the analytics vendor is registered but not connected — the exact
    # shape that produced the PostHog answer to a GitHub question.
    # Act
    need = classify_evidence_need(
        handoff_contents=("evidence_kind:service_metric_read",),
        resolved_integrations={},
        preferred_sources_for=_analytics_owns_metric_read,
    )

    # Assert: no imposed source, so the turn gathers instead of degrading.
    assert need.preferred_sources == ()
    assert need.missing == ()
    assert need.tier is not EvidenceTier.L0_DEGRADED
    assert need.required_for_authoritative is False


def test_a_named_system_metric_never_demands_an_authoritative_source() -> None:
    """The policy, not the current registration table, is what prevents this.

    Asserting only against today's registrations passes even if the kind gains
    ``requires_authoritative_source`` — no vendor claims the kind yet, so the
    authoritative branch is skipped either way. Pin the policy directly.
    """
    assert policy_for(EvidenceKind.SERVICE_METRIC_READ).requires_authoritative_source is False


def test_a_claimed_named_system_metric_still_does_not_degrade() -> None:
    # Arrange: a hostile registry that claims every kind, so the only thing
    # left standing between a GitHub question and the analytics vendor is
    # the kind's own policy.
    def _analytics_claims_everything(kind: EvidenceKind) -> tuple[str, ...]:
        _ = kind
        return ("posthog_mcp",)

    # Act
    service = classify_evidence_need(
        handoff_contents=("evidence_kind:service_metric_read",),
        resolved_integrations={},
        preferred_sources_for=_analytics_claims_everything,
    )
    product = classify_evidence_need(
        handoff_contents=("evidence_kind:metric_read",),
        resolved_integrations={},
        preferred_sources_for=_analytics_claims_everything,
    )

    # Assert: same registry, opposite outcomes — the kind decides.
    assert service.tier is not EvidenceTier.L0_DEGRADED
    assert service.required_for_authoritative is False
    assert product.tier is EvidenceTier.L0_DEGRADED


def test_product_analytics_metrics_still_demand_the_analytics_vendor() -> None:
    # Arrange / Act: the regression guard — metric_read must be untouched.
    need = classify_evidence_need(
        handoff_contents=("evidence_kind:metric_read",),
        resolved_integrations={},
        preferred_sources_for=_analytics_owns_metric_read,
    )

    # Assert
    assert need.preferred_sources == ("posthog_mcp",)
    assert need.missing == ("posthog_mcp",)
    assert need.tier is EvidenceTier.L0_DEGRADED


def test_a_named_system_metric_still_answers_with_a_number_not_an_investigation() -> None:
    # Arrange / Act
    need = classify_evidence_need(
        handoff_contents=("evidence_kind:service_metric_read",),
        resolved_integrations={"github": {"token": "x"}},
        preferred_sources_for=_analytics_owns_metric_read,
    )

    # Assert: a count question closes on the count, as metric_read does.
    assert should_suppress_investigation_offer(need) is True


def test_a_named_system_metric_attaches_the_session_goal_when_the_flag_is_omitted() -> None:
    # Arrange: planner sets the kind and leaves session_goal unset.
    handoff = AssistantHandoff.from_tool_input(
        {
            HandoffField.CONTENT: "error rate on github",
            HandoffField.EVIDENCE_KIND: "service_metric_read",
        }
    )

    # Act / Assert: the host loop must keep going until the number is delivered.
    assert handoff.evidence_kind is EvidenceKind.SERVICE_METRIC_READ
    assert handoff.session_goal is True
    assert policy_for(EvidenceKind.SERVICE_METRIC_READ).implies_session_goal is True


def test_the_unformed_metric_floor_stays_out_of_a_named_system_answer() -> None:
    """No draft HogQL and no analytics setup line under a GitHub answer.

    The floor exists so a product-analytics turn that never ran a live query
    still shows a labelled draft. Applying it to a question the user pointed at
    another system is what printed ``<github_error_event>`` placeholders.
    """
    # Arrange: a turn that named its own system and ran no analytics query.
    need = EvidenceNeed(
        kind=EvidenceKind.SERVICE_METRIC_READ,
        preferred_sources=(),
        connected=("github",),
        missing=(),
        tier=EvidenceTier.L1,
        required_for_authoritative=False,
    )
    answer = "GitHub Actions: 3 failed, 59 succeeded — a 5% failure rate."

    # Act
    text = apply_unformed_metric_floor(
        answer,
        need,
        observation="Tool: github_cli\nArguments: {}\nResult: []",
        setup_command_for=lambda name: f"/integrations setup {name}",
    )

    # Assert: the answer is returned untouched.
    assert text == answer
    assert "/integrations setup" not in text
    assert "```sql" not in text
