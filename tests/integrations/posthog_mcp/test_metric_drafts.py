"""PostHog MCP metric-draft registration and signup-cohort identity."""

from __future__ import annotations

import pytest

from core.agent_harness.turns.gather_observation import GatheredEvidence
from integrations.posthog_mcp.metric_drafts import (
    posthog_signup_cohort_resolved,
    register_posthog_mcp_metric_drafts,
)
from platform.harness_ports import (
    clear_metric_query_drafts,
    metric_query_draft_for,
)


@pytest.fixture(autouse=True)
def _register_drafts() -> None:
    clear_metric_query_drafts()
    register_posthog_mcp_metric_drafts()
    yield
    clear_metric_query_drafts()


def test_posthog_registers_count_and_cohort_hogql_drafts() -> None:
    count = metric_query_draft_for(("posthog_mcp",), cohort_goal=False)
    cohort = metric_query_draft_for(("posthog_mcp",), cohort_goal=True)
    assert count is not None and "```sql" in count
    assert "uniq(person_id)" in (count or "")
    assert cohort is not None and "<signup_event>" in cohort


def _evidence_querying(event: str) -> GatheredEvidence:
    return GatheredEvidence(
        observation=(
            "Tool: call_posthog_tool\nArguments: {}\n"
            f"Result: SELECT count() FROM events WHERE event = '{event}'"
        )
    )


def test_a_signup_event_query_resolves_signup_identity() -> None:
    evidence = _evidence_querying("user_signed_up")
    assert posthog_signup_cohort_resolved(evidence, "D7 retention was 41%") is True


@pytest.mark.parametrize("event", ["user_signed_in", "login", "$identify"])
def test_a_login_event_query_does_not_resolve_signup_identity(event: str) -> None:
    evidence = _evidence_querying(event)
    assert posthog_signup_cohort_resolved(evidence, "D7 retention was 41%") is False


def test_an_unrelated_event_query_does_not_resolve_signup_identity() -> None:
    evidence = _evidence_querying("$pageview")
    assert posthog_signup_cohort_resolved(evidence, "D7 retention was 41%") is False
