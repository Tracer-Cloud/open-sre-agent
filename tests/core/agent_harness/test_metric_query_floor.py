"""When a metric gather never ran a live query, the answer still has a draft + setup slash."""

from __future__ import annotations

import pytest

from core.agent_harness.session_goal.goal import SessionGoal
from core.agent_harness.turns.cohort_identity import (
    goal_needs_cohort_identity,
    reply_reports_cohort_unverified,
)
from core.agent_harness.turns.evidence_kind import EvidenceKind
from core.agent_harness.turns.evidence_need import EvidenceNeed, EvidenceTier
from core.agent_harness.turns.gather_observation import GatheredEvidence
from core.agent_harness.turns.metric_query_floor import (
    apply_unformed_metric_floor,
    gather_formed_live_metric_query,
)
from integrations.grafana.metric_drafts import register_grafana_metric_drafts
from integrations.posthog_mcp.metric_drafts import register_posthog_mcp_metric_drafts
from platform.harness_ports import clear_metric_query_drafts


@pytest.fixture(autouse=True)
def _register_vendor_metric_drafts() -> None:
    clear_metric_query_drafts()
    register_posthog_mcp_metric_drafts()
    register_grafana_metric_drafts()
    yield
    clear_metric_query_drafts()


def _need(*, connected: tuple[str, ...] = ("posthog_mcp",)) -> EvidenceNeed:
    missing = () if connected else ("posthog_mcp",)
    return EvidenceNeed(
        kind=EvidenceKind.METRIC_READ,
        preferred_sources=("posthog_mcp",),
        connected=connected,
        missing=missing,
        tier=EvidenceTier.L1 if connected else EvidenceTier.L0_DEGRADED,
        required_for_authoritative=True,
    )


def test_roster_and_schema_probes_are_not_a_live_metric_query() -> None:
    observation = (
        "Tool: list_posthog_tools\nArguments: {}\nResult: []\n\n"
        "Tool: call_posthog_tool\n"
        'Arguments: {"tool_name": "event-definitions"}\n'
        "Result: {events: []}"
    )
    evidence = GatheredEvidence(observation=observation, tool_results=())
    assert gather_formed_live_metric_query(evidence) is False


def test_execute_sql_is_a_live_metric_query() -> None:
    observation = (
        "Tool: call_posthog_tool\n"
        'Arguments: {"tool_name": "execute-sql", "arguments": {"query": "SELECT 1"}}\n'
        "Result: windows|272"
    )
    evidence = GatheredEvidence(observation=observation, tool_results=())
    assert gather_formed_live_metric_query(evidence) is True


def test_non_metric_fetch_does_not_count_as_live_metric_query() -> None:
    """issue_get / alert-rule reads must not suppress the draft-query floor."""
    observation = (
        "Tool: call_posthog_tool\n"
        'Arguments: {"tool_name": "issue_get", "arguments": {"id": "1"}}\n'
        "Result: {ok: true}\n\n"
        "Tool: query_grafana_alert_rules\nArguments: {}\nResult: []"
    )
    evidence = GatheredEvidence(observation=observation, tool_results=())
    assert gather_formed_live_metric_query(evidence) is False
    text = apply_unformed_metric_floor(
        "Looked up related issues but have no count.",
        _need(),
        observation=observation,
        setup_command_for=lambda name: f"/integrations setup {name}",
    )
    assert "```sql" in text
    assert "/integrations setup posthog_mcp" in text


def test_unformed_floor_appends_draft_hogql_and_setup_slash() -> None:
    """Connected analytics, no count query → draft fence + one setup command."""
    observation = (
        "Tool: list_posthog_tools\nArguments: {}\nResult: []\n\n"
        "Tool: call_posthog_tool\n"
        'Arguments: {"tool_name": "event-definitions"}\n'
        "Result: []"
    )
    text = apply_unformed_metric_floor(
        "I could not identify a signup event, so I cannot return a count.",
        _need(),
        observation=observation,
        setup_command_for=lambda name: f"/integrations setup {name}",
    )
    assert "```sql" in text
    assert "SELECT" in text
    assert "not a live count" in text.lower() or "draft" in text.lower()
    assert text.count("/integrations setup posthog_mcp") == 1


def test_unformed_floor_does_not_duplicate_existing_draft_and_cta() -> None:
    body = (
        "No live count.\n\n"
        "```sql\nSELECT uniq(person_id) FROM events\n```\n\n"
        "/integrations setup posthog_mcp"
    )
    text = apply_unformed_metric_floor(
        body,
        _need(),
        observation="Tool: list_posthog_tools\nResult: []",
        setup_command_for=lambda name: f"/integrations setup {name}",
    )
    assert text.count("```sql") == 1
    assert text.count("/integrations setup posthog_mcp") == 1


def test_source_labeled_hogql_error_does_not_get_setup_cta() -> None:
    """HogQL syntax failures already ran a query — stay L1, no reconnect slash."""
    observation = "Tool: posthog_mcp\nResult: HogQL syntax error near WHERE"
    evidence = GatheredEvidence(observation=observation, tool_results=())
    original = "The HogQL query failed; try a simpler property."
    assert gather_formed_live_metric_query(evidence, metric_source_ids=("posthog_mcp",)) is True
    text = apply_unformed_metric_floor(
        original,
        _need(),
        observation=observation,
        setup_command_for=lambda name: f"/integrations setup {name}",
    )
    assert text == original


def test_grafana_unformed_floor_uses_promql() -> None:
    need = EvidenceNeed(
        kind=EvidenceKind.METRIC_READ,
        preferred_sources=("grafana",),
        connected=("grafana",),
        missing=(),
        tier=EvidenceTier.L1,
        required_for_authoritative=True,
    )
    text = apply_unformed_metric_floor(
        "Grafana timed out before a query ran.",
        need,
        observation="Tool: list_grafana_tools\nResult: []",
        setup_command_for=lambda name: f"/integrations setup {name}",
    )
    assert "```promql" in text
    assert "/integrations setup grafana" in text
    observation = (
        'Tool: call_posthog_tool\nArguments: {"tool_name": "execute-sql"}\nResult: windows|272'
    )
    original = "I found: 272 Windows users."
    text = apply_unformed_metric_floor(
        original,
        _need(),
        observation=observation,
        setup_command_for=lambda name: f"/integrations setup {name}",
    )
    assert text == original


def test_signup_goal_unverified_after_live_probes_gets_draft_without_reconnect() -> None:
    """Connected analytics ran candidate queries; signup still unresolved → draft only."""
    observation = (
        "Tool: call_posthog_tool\n"
        'Arguments: {"tool_name": "event-definitions"}\n'
        "Result: ['$pageview', 'login']\n\n"
        "Tool: call_posthog_tool\n"
        'Arguments: {"tool_name": "execute-sql", '
        '"arguments": {"query": "SELECT 1 WHERE event = \'user_signed_up\'"}}\n'
        "Result: 0\n\n"
        "Tool: call_posthog_tool\n"
        'Arguments: {"tool_name": "execute-sql", '
        '"arguments": {"query": "SELECT 1 WHERE event = \'signed_up\'"}}\n'
        "Result: 0"
    )
    reply = (
        "Live PostHog returned no eligible Windows signups, so D7 retention is "
        "unavailable, not 0%. The query recognized none of these candidate "
        "signup events: user_signed_up, signed_up. signup event unverified."
    )
    session = type(
        "S",
        (),
        {
            "session_goal": SessionGoal(
                condition=(
                    "What is D7 retention for users who signed up on Windows in the last 30 days?"
                ),
                max_outer_turns=4,
                host_owned=True,
            )
        },
    )()
    assert goal_needs_cohort_identity(session.session_goal.condition)
    assert reply_reports_cohort_unverified(reply)
    text = apply_unformed_metric_floor(
        reply,
        _need(connected=("posthog_mcp",)),
        observation=observation,
        setup_command_for=lambda name: f"/integrations setup {name}",
        session=session,
    )
    assert "```sql" in text
    assert "<signup_event>" in text
    assert "/integrations setup" not in text


def test_signup_goal_guessed_sql_event_without_schema_keeps_floor() -> None:
    """Guessing event='user_signed_up' in HogQL must not count as verified identity."""
    observation = (
        "Tool: call_posthog_tool\n"
        'Arguments: {"tool_name": "execute-sql", '
        '"arguments": {"query": "SELECT … WHERE event = \'user_signed_up\'"}}\n'
        "Result: 12%"
    )
    reply = "D7 retention is 12% for Windows signups (event user_signed_up)."
    session = type(
        "S",
        (),
        {
            "session_goal": SessionGoal(
                condition="D7 retention for users who signed up on Windows",
                max_outer_turns=4,
                host_owned=True,
            )
        },
    )()
    text = apply_unformed_metric_floor(
        reply,
        _need(),
        observation=observation,
        setup_command_for=lambda name: f"/integrations setup {name}",
        session=session,
    )
    assert "```sql" in text
    assert "<signup_event>" in text


def test_signup_goal_verified_live_percent_skips_floor() -> None:
    observation = (
        "Tool: call_posthog_tool\n"
        'Arguments: {"tool_name": "event-definitions"}\n'
        "Result: ['user_signed_up', '$pageview']\n\n"
        "Tool: call_posthog_tool\n"
        'Arguments: {"tool_name": "execute-sql", '
        '"arguments": {"query": "SELECT … WHERE event = \'user_signed_up\'"}}\n'
        "Result: 12%"
    )
    original = "D7 retention is 12% for Windows signups (event user_signed_up)."
    session = type(
        "S",
        (),
        {
            "session_goal": SessionGoal(
                condition="D7 retention for users who signed up on Windows",
                max_outer_turns=4,
                host_owned=True,
            )
        },
    )()
    text = apply_unformed_metric_floor(
        original,
        _need(),
        observation=observation,
        setup_command_for=lambda name: f"/integrations setup {name}",
        session=session,
    )
    assert text == original


def test_cohort_floor_applies_with_grafana_only_preferred_source() -> None:
    """Product cohort policy is not PostHog-gated — any preferred source drafts."""
    need = EvidenceNeed(
        kind=EvidenceKind.METRIC_READ,
        preferred_sources=("grafana",),
        connected=("grafana",),
        missing=(),
        tier=EvidenceTier.L1,
        required_for_authoritative=True,
    )
    reply = "signup event unverified — cannot provide a retention percentage."
    session = type(
        "S",
        (),
        {
            "session_goal": SessionGoal(
                condition="D7 retention for users who signed up last month",
                max_outer_turns=4,
                host_owned=True,
            )
        },
    )()
    text = apply_unformed_metric_floor(
        reply,
        need,
        observation="Tool: query_grafana_metrics\nArguments: {}\nResult: []",
        setup_command_for=lambda name: f"/integrations setup {name}",
        session=session,
    )
    assert "```promql" in text
    assert "/integrations setup" not in text


def test_unformed_floor_wraps_a_bare_setup_command_in_backticks_once() -> None:
    """The appended command is code-formatted, and never double-wrapped."""
    # Arrange
    bare = apply_unformed_metric_floor(
        "No live count.",
        _need(),
        observation="Tool: list_posthog_tools\nResult: []",
        setup_command_for=lambda name: f"/connect {name}",
    )
    already_wrapped = apply_unformed_metric_floor(
        "No live count.",
        _need(),
        observation="Tool: list_posthog_tools\nResult: []",
        setup_command_for=lambda name: f"`/connect {name}`",
    )

    # Assert
    assert "`/connect posthog_mcp`" in bare
    assert "`/connect posthog_mcp`" in already_wrapped
    assert "``/connect" not in already_wrapped


def test_unformed_floor_omits_setup_when_the_surface_has_no_command() -> None:
    """An empty command must append nothing rather than an empty code fence."""
    text = apply_unformed_metric_floor(
        "No live count.",
        _need(),
        observation="Tool: list_posthog_tools\nResult: []",
        setup_command_for=lambda _name: "",
    )

    assert "/connect" not in text
