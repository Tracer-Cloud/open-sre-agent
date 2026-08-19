"""Contracts for GitHub's action-agent routing guidance."""

from integrations.github.action_prompt import github_action_prompt_fragment


def test_star_velocity_routes_to_one_turn_gather_without_metric_classification() -> None:
    prompt = github_action_prompt_fragment()

    assert "assistant_handoff(requires_gather=true)" in prompt
    assert "not a product-analytics metric_read" in prompt
    assert "omit evidence_kind" in prompt
    assert "session_goal" in prompt
