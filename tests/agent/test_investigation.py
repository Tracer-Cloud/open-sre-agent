from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from app.agent.investigation import ConnectedInvestigationAgent, _availability_view


def test_availability_view_marks_configured_integrations_without_mutating_state() -> None:
    resolved = {"github": {"access_token": "token"}, "_all": [{"service": "github"}]}

    view = _availability_view(resolved)

    assert view["github"]["connection_verified"] is True
    assert "connection_verified" not in resolved["github"]
    assert view["_all"] == resolved["_all"]


def test_run_gracefully_handles_model_not_found_runtime_error() -> None:
    """When the LLM raises a model-not-found RuntimeError, the agent should
    return a degraded state dict instead of crashing the pipeline."""
    mock_llm = MagicMock()
    mock_llm.invoke.side_effect = RuntimeError("OpenAI model 'qwen' not found.")
    mock_llm.tool_schemas.return_value = []

    mock_tracker = MagicMock()

    with (
        patch("app.agent.investigation.get_agent_llm", return_value=mock_llm),
        patch("app.agent.investigation.get_tracker", return_value=mock_tracker),
    ):
        agent = ConnectedInvestigationAgent()
        state = {
            "alert_name": "Test alert",
            "pipeline_name": "test-pipeline",
            "severity": "critical",
            "resolved_integrations": {},
        }
        result = agent.run(state)

    mock_tracker.error.assert_called_once_with(
        "investigation_agent", message="Failed: Model not found"
    )
    assert result["root_cause_category"] == "Configuration Error"
    assert result["validity_score"] == 0.0
    assert "not found" in result["root_cause"].lower()
    assert result["remediation_steps"]
    assert result["causal_chain"]


def test_run_re_raises_unmatched_runtime_error() -> None:
    """RuntimeError messages that do not match the model-not-found heuristic
    should be re-raised so upstream handlers can deal with them."""
    mock_llm = MagicMock()
    mock_llm.invoke.side_effect = RuntimeError("Some other API failure")
    mock_llm.tool_schemas.return_value = []

    mock_tracker = MagicMock()

    with (
        patch("app.agent.investigation.get_agent_llm", return_value=mock_llm),
        patch("app.agent.investigation.get_tracker", return_value=mock_tracker),
    ):
        agent = ConnectedInvestigationAgent()
        state = {
            "alert_name": "Test alert",
            "pipeline_name": "test-pipeline",
            "severity": "critical",
            "resolved_integrations": {},
        }
        with pytest.raises(RuntimeError, match="Some other API failure"):
            agent.run(state)

    mock_tracker.error.assert_not_called()
