"""PostHog cohort / signup-retention identity registration."""

from __future__ import annotations

import pytest

from integrations.posthog_mcp.cohort_identity import (
    posthog_goal_needs_cohort_identity,
    posthog_reply_reports_cohort_unverified,
    register_posthog_mcp_cohort_identity,
)
from platform.harness_ports import (
    clear_metric_query_drafts,
    metric_goal_needs_cohort_identity,
    metric_reply_reports_cohort_unverified,
)


@pytest.fixture(autouse=True)
def _register_cohort_identity() -> None:
    clear_metric_query_drafts()
    register_posthog_mcp_cohort_identity()
    yield
    clear_metric_query_drafts()


@pytest.mark.parametrize(
    "condition",
    [
        "how many users signed up last week",
        "signup count for windows users",
        "d7 retention for users who signed up in march",
        "retention by user cohort",
    ],
)
def test_signup_and_user_cohort_conditions_match_posthog_policy(condition: str) -> None:
    assert posthog_goal_needs_cohort_identity(condition) is True
    assert metric_goal_needs_cohort_identity(condition) is True


@pytest.mark.parametrize(
    "condition",
    [
        "data retention window for logs",
        "log retention 30 days",
        "what is our backup retention policy",
        "improve employee retention",
    ],
)
def test_storage_retention_questions_do_not_match_posthog_cohort_policy(
    condition: str,
) -> None:
    assert posthog_goal_needs_cohort_identity(condition) is False
    assert metric_goal_needs_cohort_identity(condition) is False


def test_unverified_mark_is_detected_through_the_port() -> None:
    reply = "signup event unverified — cannot provide a retention percentage."
    assert posthog_reply_reports_cohort_unverified(reply) is True
    assert metric_reply_reports_cohort_unverified(reply) is True


def test_without_registration_core_sees_no_cohort_policy() -> None:
    clear_metric_query_drafts()
    assert metric_goal_needs_cohort_identity("how many users signed up") is False
    assert metric_reply_reports_cohort_unverified("signup event unverified") is False
