"""When a metric gather never ran a live query, the answer still has a draft + setup slash."""

from __future__ import annotations

from core.agent_harness.turns.evidence_kind import EvidenceKind
from core.agent_harness.turns.evidence_need import EvidenceNeed, EvidenceTier
from core.agent_harness.turns.gather_observation import GatheredEvidence
from core.agent_harness.turns.metric_query_floor import (
    apply_unformed_metric_floor,
    gather_formed_live_metric_query,
)


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
    """S2: connected PostHog, no count query → draft HogQL + one setup command."""
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
