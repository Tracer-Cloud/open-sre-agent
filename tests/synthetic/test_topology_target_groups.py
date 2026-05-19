"""Regression: topology.target_group_arn accepts string or list (#2100)."""

from __future__ import annotations

import pytest

from tests.synthetic.schemas import validate_scenario_metadata

_BASE: dict[str, object] = {
    "schema_version": "1.0",
    "scenario_id": "999-multi-tg-test",
    "engine": "postgres",
    "engine_version": "15",
    "instance_class": "db.r6g.large",
    "region": "us-east-1",
    "db_instance_identifier": "test-db",
    "failure_mode": "cpu_saturation",
    "severity": "P2",
    "available_evidence": ["aws_cloudwatch_metrics"],
}


def test_string_form_target_group_arn_normalises_to_singleton_list() -> None:
    """Back-compat: scenarios 001–020 pass a bare string; loader must surface
    ``target_group_arns`` as a singleton list so downstream consumers don't
    have to branch on the field shape."""
    fixture = {
        **_BASE,
        "topology": {
            "vpc_id": "vpc-aaa",
            "load_balancer_arn": "arn:aws:elasticloadbalancing:us-east-1:111:loadbalancer/app/web/abc",
            "target_group_arn": "arn:aws:elasticloadbalancing:us-east-1:111:targetgroup/web-tg/abc",
            "tiers": [{"name": "web", "instance_ids": ["i-1"]}],
        },
    }

    validated = validate_scenario_metadata(fixture)
    topology = validated["topology"]
    assert topology["target_group_arns"] == [
        "arn:aws:elasticloadbalancing:us-east-1:111:targetgroup/web-tg/abc"
    ]
    # Original field is left in place so existing readers don't break.
    assert topology["target_group_arn"] == (
        "arn:aws:elasticloadbalancing:us-east-1:111:targetgroup/web-tg/abc"
    )


def test_list_form_target_group_arn_preserves_order_and_count() -> None:
    """Multi-tenant noisy-neighbour shape (#1832 review): the topology block
    enumerates every tg arn explicitly so the trajectory-budget grader doesn't
    need to lean on agent-side ``get_elb_target_health`` discovery."""
    arns = [
        "arn:aws:elasticloadbalancing:us-east-1:111:targetgroup/acme-tg/abc",
        "arn:aws:elasticloadbalancing:us-east-1:111:targetgroup/zenith-tg/def",
        "arn:aws:elasticloadbalancing:us-east-1:111:targetgroup/orbit-tg/ghi",
    ]
    fixture = {
        **_BASE,
        "topology": {
            "vpc_id": "vpc-bbb",
            "load_balancer_arn": "arn:aws:elasticloadbalancing:us-east-1:111:loadbalancer/app/multi/xyz",
            "target_group_arn": arns,
            "tiers": [
                {"name": "acme", "instance_ids": ["i-a"]},
                {"name": "zenith", "instance_ids": ["i-z"]},
                {"name": "orbit", "instance_ids": ["i-o"]},
            ],
        },
    }

    validated = validate_scenario_metadata(fixture)
    assert validated["topology"]["target_group_arns"] == arns


def test_explicit_target_group_arns_field_takes_precedence() -> None:
    """If a scenario sets ``target_group_arns`` directly (no inline list in
    ``target_group_arn``), the loader honours it as-is."""
    arns = ["arn:tg-A", "arn:tg-B"]
    fixture = {
        **_BASE,
        "topology": {
            "vpc_id": "vpc-ccc",
            "load_balancer_arn": "arn:lb",
            "target_group_arns": arns,
            "tiers": [{"name": "web", "instance_ids": ["i-1"]}],
        },
    }

    validated = validate_scenario_metadata(fixture)
    assert validated["topology"]["target_group_arns"] == arns


def test_missing_topology_does_not_synthesise_target_group_arns() -> None:
    """Legacy RDS-only scenarios (000–014) don't carry a topology block at all;
    the loader must not invent one."""
    validated = validate_scenario_metadata(dict(_BASE))
    assert "topology" not in validated


def test_invalid_target_group_arn_shape_raises() -> None:
    """Numeric values or empty list entries must fail loud during loading."""
    fixture = {
        **_BASE,
        "topology": {
            "vpc_id": "vpc-ddd",
            "load_balancer_arn": "arn:lb",
            "target_group_arn": ["arn:tg-A", ""],
            "tiers": [{"name": "web", "instance_ids": ["i-1"]}],
        },
    }

    with pytest.raises(ValueError, match="target_group_arn"):
        validate_scenario_metadata(fixture)
