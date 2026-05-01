"""Unit tests for ``app/nodes/investigate/parallel.py`` and ``merge.py``.

These are the two most critical nodes in the investigation pipeline:
- ``node_investigate_hypothesis``: executes a single tool call in its own subgraph.
- ``merge_hypothesis_results``: merges parallel results back into agent state.

Previously neither function had any unit tests. These cover the core paths
without requiring live LLM or tool infrastructure.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from app.nodes.investigate.execution.execute_actions import ActionExecutionResult
from app.nodes.investigate.parallel import node_investigate_hypothesis


# ─── Helpers ──────────────────────────────────────────────────────────────────

def _make_state(**kwargs: Any) -> dict[str, Any]:
    defaults: dict[str, Any] = {
        "action_to_run": None,
        "available_sources": {},
        "investigation_loop_count": 0,
        "evidence": {},
        "executed_hypotheses": [],
        "planned_actions": [],
        "hypothesis_results": [],
    }
    return {**defaults, **kwargs}


def _make_action(name: str) -> MagicMock:
    action = MagicMock()
    action.name = name
    return action


# ─── node_investigate_hypothesis ──────────────────────────────────────────────

class TestNodeInvestigateHypothesis:

    def test_returns_empty_when_no_action_to_run(self) -> None:
        state = _make_state(action_to_run=None)
        result = node_investigate_hypothesis(state)
        assert result == {"hypothesis_results": []}

    def test_returns_empty_when_action_not_in_registry(self) -> None:
        state = _make_state(action_to_run="nonexistent_action")
        with patch(
            "app.nodes.investigate.parallel.get_available_actions",
            return_value=[_make_action("other_action")],
        ):
            result = node_investigate_hypothesis(state)
        assert result == {"hypothesis_results": []}

    def test_successful_execution_returns_result(self) -> None:
        action_name = "get_eks_events"
        state = _make_state(
            action_to_run=action_name,
            available_sources={"eks": {"cluster": "prod-cluster"}},
        )
        mock_result = ActionExecutionResult(
            action_name=action_name,
            success=True,
            data={"events": [{"reason": "OOMKilled"}]},
            error=None,
        )
        with patch(
            "app.nodes.investigate.parallel.get_available_actions",
            return_value=[_make_action(action_name)],
        ):
            with patch(
                "app.nodes.investigate.parallel.execute_actions",
                return_value={action_name: mock_result},
            ):
                result = node_investigate_hypothesis(state)

        assert len(result["hypothesis_results"]) == 1
        hr = result["hypothesis_results"][0]
        assert hr["action_name"] == action_name
        assert hr["success"] is True
        assert hr["data"] == {"events": [{"reason": "OOMKilled"}]}
        assert hr["error"] is None

    def test_failed_execution_propagates_error(self) -> None:
        action_name = "list_eks_pods"
        state = _make_state(action_to_run=action_name)
        mock_result = ActionExecutionResult(
            action_name=action_name,
            success=False,
            data={},
            error="Connection refused",
        )
        with patch(
            "app.nodes.investigate.parallel.get_available_actions",
            return_value=[_make_action(action_name)],
        ):
            with patch(
                "app.nodes.investigate.parallel.execute_actions",
                return_value={action_name: mock_result},
            ):
                result = node_investigate_hypothesis(state)

        hr = result["hypothesis_results"][0]
        assert hr["success"] is False
        assert hr["error"] == "Connection refused"

    def test_returns_empty_when_execute_actions_returns_no_result(self) -> None:
        action_name = "query_datadog_logs"
        state = _make_state(action_to_run=action_name)
        with patch(
            "app.nodes.investigate.parallel.get_available_actions",
            return_value=[_make_action(action_name)],
        ):
            with patch(
                "app.nodes.investigate.parallel.execute_actions",
                return_value={},  # action ran but produced no entry
            ):
                result = node_investigate_hypothesis(state)

        assert result == {"hypothesis_results": []}


# ─── merge_hypothesis_results ─────────────────────────────────────────────────

class TestMergeHypothesisResults:
    """Tests for ``app/nodes/investigate/merge.py::merge_hypothesis_results``."""

    def _minimal_state(self, **kwargs: Any) -> dict[str, Any]:
        defaults: dict[str, Any] = {
            "raw_alert": {"alert_name": "test-alert"},
            "evidence": {},
            "executed_hypotheses": [],
            "hypothesis_results": [],
            "available_sources": {},
            "plan_rationale": "",
            "investigation_loop_count": 0,
            "plan_audit": None,
            "resolved_integrations": {},
            "masking_map": {},
        }
        return {**defaults, **kwargs}

    def test_empty_hypothesis_results_produces_valid_output(self) -> None:
        from app.nodes.investigate.merge import merge_hypothesis_results

        state = self._minimal_state(hypothesis_results=[])
        with patch(
            "app.nodes.investigate.merge._load_opensre_telemetry_into_evidence",
            return_value=({}, None),
        ):
            result = merge_hypothesis_results(state)

        assert "evidence" in result
        assert "executed_hypotheses" in result
        assert "available_sources" in result

    def test_grafana_service_name_updated_when_no_logs_yet(self) -> None:
        """Discovered service names should update available_sources without mutating input."""
        from app.nodes.investigate.merge import merge_hypothesis_results

        original_sources: dict[str, Any] = {
            "grafana": {"service_name": "payments", "pipeline_name": "payments"}
        }
        state = self._minimal_state(
            available_sources=original_sources,
            hypothesis_results=[],
        )

        patched_evidence = {
            "grafana_service_names": ["payments-api", "payments-worker"],
        }
        with patch(
            "app.nodes.investigate.merge._load_opensre_telemetry_into_evidence",
            return_value=({}, None),
        ):
            with patch(
                "app.nodes.investigate.merge.summarize_execution_results",
                return_value=(patched_evidence, [], "summary"),
            ):
                result = merge_hypothesis_results(state)

        # The INPUT state must not have been mutated (Bug 6 regression test).
        assert original_sources["grafana"]["service_name"] == "payments", (
            "merge_hypothesis_results must not mutate the input state dict"
        )
        # The OUTPUT should have the updated service name.
        out_sources = result.get("available_sources", {})
        assert out_sources.get("grafana", {}).get("service_name") in (
            "payments-api",
            "payments-worker",
            "payments",  # no match found, unchanged
        )

    def test_masking_applied_to_evidence(self) -> None:
        """Evidence returned by merge must go through the masking pipeline."""
        from app.nodes.investigate.merge import merge_hypothesis_results

        state = self._minimal_state(hypothesis_results=[])
        with patch(
            "app.nodes.investigate.merge._load_opensre_telemetry_into_evidence",
            return_value=({}, None),
        ):
            with patch(
                "app.nodes.investigate.merge.MaskingContext"
            ) as MockMaskingCtx:
                mock_ctx = MagicMock()
                mock_ctx.mask_value.return_value = {"masked": True}
                mock_ctx.to_state.return_value = {}
                MockMaskingCtx.from_state.return_value = mock_ctx

                result = merge_hypothesis_results(state)

        mock_ctx.mask_value.assert_called_once()
        assert result["evidence"] == {"masked": True}
