"""Tests for Sentry issue scoring helpers."""

from __future__ import annotations

from integrations.sentry.issue_scoring import business_impact_score


def test_business_impact_score_prefers_operational_blocker_over_volume() -> None:
    noisy_score, _ = business_impact_score({"title": "metadata 400", "count": 568, "userCount": 0})
    blocker_score, reasons = business_impact_score(
        {
            "title": "LLMCreditExhaustedError: OpenAI credit exhausted",
            "count": 51,
            "userCount": 0,
        }
    )
    assert blocker_score > noisy_score
    assert "LLM billing or quota exhaustion" in reasons
