"""Unit tests for app.tools.ELBTargetHealthTool."""

from __future__ import annotations

from typing import Any

import pytest

from app.tools.ELBTargetHealthTool import _is_available, get_elb_target_health
from app.tools.utils.aws_topology_helper import extract_target_health_params


class _FakeAWSBackend:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    def describe_target_health(
        self,
        target_group_arn: str = "",
        load_balancer_arn: str = "",
        **_: Any,
    ) -> dict[str, Any]:
        self.calls.append({"tg": target_group_arn, "lb": load_balancer_arn})
        return {
            "source": "ec2",
            "available": True,
            "target_groups": [{"TargetGroupArn": target_group_arn or "tg-x"}],
            "healthy_targets": [{"instance_id": "i-1", "state": "healthy"}],
            "unhealthy_targets": [],
            "instance_ids": ["i-1"],
            "error": None,
        }


def test_is_available_requires_topology_hints() -> None:
    assert _is_available({}) is False
    assert _is_available({"ec2": {"connection_verified": True}}) is False
    assert (
        _is_available({"ec2": {"connection_verified": True, "target_group_arns": ["tg-1"]}}) is True
    )
    assert (
        _is_available({"ec2": {"connection_verified": True, "load_balancer_arns": ["lb-1"]}})
        is True
    )
    assert _is_available({"ec2": {"_backend": object()}}) is True


def test_extract_params_picks_first_target_group_first() -> None:
    params = extract_target_health_params(
        {
            "ec2": {
                "target_group_arns": ["tg-1", "tg-2"],
                "load_balancer_arns": ["lb-1"],
                "region": "eu-west-1",
            }
        }
    )
    assert params["target_group_arn"] == "tg-1"
    assert params["load_balancer_arn"] == "lb-1"
    assert params["region"] == "eu-west-1"


def test_extract_params_falls_back_to_load_balancer_when_no_target_group() -> None:
    params = extract_target_health_params({"ec2": {"load_balancer_arns": ["lb-1"]}})
    assert params["target_group_arn"] == ""
    assert params["load_balancer_arn"] == "lb-1"


def test_extract_params_raises_when_ec2_source_missing() -> None:
    with pytest.raises(ValueError):
        extract_target_health_params({})


def test_backend_short_circuits_boto3() -> None:
    backend = _FakeAWSBackend()
    result = get_elb_target_health(target_group_arn="tg-1", aws_backend=backend)
    assert result["available"] is True
    assert result["instance_ids"] == ["i-1"]
    assert backend.calls == [{"tg": "tg-1", "lb": ""}]


def test_returns_error_when_neither_arn_provided() -> None:
    out = get_elb_target_health()
    assert out["available"] is False
    assert "required" in out["error"]


def test_real_path_combines_groups_and_health(monkeypatch: pytest.MonkeyPatch) -> None:
    def _execute(**kwargs: Any) -> dict[str, Any]:
        if kwargs["operation_name"] == "describe_target_groups":
            return {
                "success": True,
                "data": {"TargetGroups": [{"TargetGroupArn": "tg-1"}]},
            }
        # describe_target_health
        return {
            "success": True,
            "data": {
                "TargetHealthDescriptions": [
                    {
                        "Target": {"Id": "i-healthy", "Port": 80},
                        "TargetHealth": {"State": "healthy"},
                    },
                    {
                        "Target": {"Id": "i-draining", "Port": 80},
                        "TargetHealth": {"State": "draining", "Reason": "Target.Deregistration"},
                    },
                ]
            },
        }

    monkeypatch.setattr("app.tools.ELBTargetHealthTool.execute_aws_sdk_call", _execute)
    out = get_elb_target_health(target_group_arn="tg-1")
    assert out["available"] is True
    assert [t["instance_id"] for t in out["healthy_targets"]] == ["i-healthy"]
    assert [t["instance_id"] for t in out["unhealthy_targets"]] == ["i-draining"]
    assert out["instance_ids"] == ["i-healthy", "i-draining"]


def test_summary_block_is_agent_friendly(monkeypatch: pytest.MonkeyPatch) -> None:
    """Tool output must include a precomputed summary the LLM can quote directly."""

    def _execute(**kwargs: Any) -> dict[str, Any]:
        if kwargs["operation_name"] == "describe_target_groups":
            return {
                "success": True,
                "data": {"TargetGroups": [{"TargetGroupArn": "tg-1"}]},
            }
        return {
            "success": True,
            "data": {
                "TargetHealthDescriptions": [
                    {
                        "Target": {"Id": f"i-h{i}", "Port": 80},
                        "TargetHealth": {"State": "healthy"},
                    }
                    for i in range(3)
                ]
                + [
                    {
                        "Target": {"Id": "i-d1", "Port": 80},
                        "TargetHealth": {"State": "draining"},
                    }
                ],
            },
        }

    monkeypatch.setattr("app.tools.ELBTargetHealthTool.execute_aws_sdk_call", _execute)
    out = get_elb_target_health(target_group_arn="tg-1")
    summary = out["summary"]
    assert summary["total_targets"] == 4
    assert summary["healthy_count"] == 3
    assert summary["unhealthy_count"] == 1
    assert summary["healthy_ratio_pct"] == 75.0
    assert summary["unhealthy_states"] == ["draining"]
    assert summary["target_group_count"] == 1


def test_real_path_propagates_describe_groups_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "app.tools.ELBTargetHealthTool.execute_aws_sdk_call",
        lambda **_: {"success": False, "error": "AccessDenied"},
    )
    out = get_elb_target_health(target_group_arn="tg-1")
    assert out["available"] is False
    assert "Failed" in out["error"]
