from __future__ import annotations

from app.remediation.orchestrator import run_remediation_plan


def test_orchestrator_empty_steps() -> None:
    result = run_remediation_plan([], auto_execute=False)
    assert result["remediation_plan"] == []
    assert result["remediation_results"] == []
    assert result["all_succeeded"] is True


def test_orchestrator_manual_step_skipped() -> None:
    result = run_remediation_plan(
        ["Check the logs manually"],
        auto_execute=False,
    )
    assert len(result["remediation_plan"]) == 1
    assert result["remediation_plan"][0]["action_type"] == "manual_step"
    assert len(result["remediation_results"]) == 1
    assert result["remediation_results"][0]["skipped"] is True


def test_orchestrator_auto_execute_classifies_but_skips_shell() -> None:
    result = run_remediation_plan(
        ["Restart the deployment my-app"],
        auto_execute=True,
    )
    assert len(result["remediation_plan"]) == 1
    assert result["remediation_plan"][0]["action_type"] == "kubectl_restart_deployment"
    assert len(result["remediation_results"]) == 1


def test_orchestrator_without_confirm_fn() -> None:
    result = run_remediation_plan(
        ["Restart the deployment my-app"],
        auto_execute=False,
    )
    assert len(result["remediation_results"]) == 1
    assert result["remediation_results"][0]["skipped"] is True
    assert "confirmation" in (result["remediation_results"][0].get("error") or "").lower()


def test_orchestrator_ecs_cluster_in_plan() -> None:
    result = run_remediation_plan(
        ["Restart the ECS service my-svc in cluster prod"],
        auto_execute=False,
    )
    assert len(result["remediation_plan"]) == 1
    plan = result["remediation_plan"][0]
    assert plan["action_type"] == "aws_restart_ecs_service"
    assert plan["target"] == "my-svc"
    assert "--cluster prod" in plan["command"]


def test_orchestrator_ecs_default_cluster_in_plan() -> None:
    result = run_remediation_plan(
        ["Restart the ECS service my-svc"],
        auto_execute=False,
    )
    assert len(result["remediation_plan"]) == 1
    plan = result["remediation_plan"][0]
    assert plan["action_type"] == "aws_restart_ecs_service"
    assert plan["target"] == "my-svc"
    assert "cluster default" in plan["command"]


def test_orchestrator_with_confirm_fn_yes() -> None:
    def confirm_fn(_action) -> bool:
        return True

    result = run_remediation_plan(
        ["Restart the deployment my-app"],
        auto_execute=False,
        confirm_fn=confirm_fn,
    )
    assert len(result["remediation_results"]) == 1
    assert result["remediation_results"][0].get("success") is not None


def test_orchestrator_with_confirm_fn_no() -> None:
    def confirm_fn(_action) -> bool:
        return False

    result = run_remediation_plan(
        ["Restart the deployment my-app"],
        auto_execute=False,
        confirm_fn=confirm_fn,
    )
    assert len(result["remediation_results"]) == 1
    assert result["remediation_results"][0]["skipped"] is True
    assert "rejected" in result["remediation_results"][0]["error"].lower()
