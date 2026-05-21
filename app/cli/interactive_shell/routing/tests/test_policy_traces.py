"""Policy-trace contracts for deterministic mapper and planner postprocessing."""

from __future__ import annotations

from app.cli.interactive_shell.routing.handle_message_with_agent.orchestration.interaction_models import (
    PlannedAction,
)
from app.cli.interactive_shell.routing.handle_message_with_agent.orchestration.llm_action_planner import (
    finalize_planner_result_with_trace,
)
from app.cli.interactive_shell.routing.handle_message_with_agent.orchestration.slash_commands.deterministic_action_mapper import (
    map_actions_result,
)
from app.cli.interactive_shell.routing.policy_tags import (
    DeterministicMapperPolicyTag,
    PlannerPostprocessPolicyTag,
)
from app.cli.interactive_shell.runtime.session import ReplSession


def test_mapper_result_emits_unhandled_clause_policy_tag() -> None:
    result = map_actions_result("please do this thing for me")
    assert result.has_unhandled_clause is True
    assert DeterministicMapperPolicyTag.UNHANDLED_CLAUSE_DETECTED in result.applied_policies


def test_mapper_result_waives_investigation_only_unhandled_clauses() -> None:
    result = map_actions_result('investigate "checkout errors" and send it "last 15 minutes"')
    assert result.has_unhandled_clause is False
    assert (
        DeterministicMapperPolicyTag.INVESTIGATION_ONLY_UNHANDLED_WAIVED in result.applied_policies
    )


def test_planner_policy_trace_marks_vague_local_model_fail_closed() -> None:
    result = finalize_planner_result_with_trace(
        "please connect to local llama",
        [],
        False,
    )
    assert result.actions == []
    assert result.has_unhandled is True
    assert result.applied_policies == (PlannerPostprocessPolicyTag.FAIL_CLOSED_VAGUE_LOCAL_MODEL,)


def test_planner_policy_trace_marks_compound_reconciliation() -> None:
    llm_actions = [
        PlannedAction(
            kind="slash",
            content="/health",
            position=0,
            source="llm",
            target_surface="slash",
        )
    ]
    result = finalize_planner_result_with_trace(
        "check opensre health and show connected services",
        llm_actions,
        False,
    )
    assert [action.content for action in result.actions] == ["/health", "/list integrations"]
    assert (
        PlannerPostprocessPolicyTag.RECONCILE_COMPOUND_WITH_DETERMINISTIC in result.applied_policies
    )


def test_planner_policy_trace_marks_unconfigured_integration_fail_closed() -> None:
    session = ReplSession(
        configured_integrations_known=True,
        configured_integrations=(),
    )
    result = finalize_planner_result_with_trace(
        "show datadog integration details",
        [
            PlannedAction(
                kind="slash",
                content="/integrations show datadog",
                position=0,
                source="llm",
                target_surface="slash",
            )
        ],
        False,
        session=session,
    )
    assert len(result.actions) == 1
    assert result.actions[0].kind == "assistant_handoff"
    assert (
        PlannerPostprocessPolicyTag.FAIL_CLOSED_UNCONFIGURED_INTEGRATION_DETAIL
        in result.applied_policies
    )


def test_planner_policy_trace_marks_incident_paste_coercion() -> None:
    message = "checkout API is returning HTTP 500 for 30% of requests in us-east-1"
    result = finalize_planner_result_with_trace(
        message,
        [
            PlannedAction(
                kind="investigation",
                content=message,
                position=0,
                source="llm",
                target_surface="investigation",
            )
        ],
        False,
    )
    assert len(result.actions) == 1
    assert result.actions[0].kind == "assistant_handoff"
    assert PlannerPostprocessPolicyTag.COERCE_INCIDENT_PASTE_HANDOFF in result.applied_policies
