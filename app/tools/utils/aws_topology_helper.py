"""Shared helpers for EC2/ELB topology investigation tools."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any


def build_ec2_summary(
    instances: Sequence[Mapping[str, Any]],
    by_tier: Mapping[str, Sequence[str]],
) -> dict[str, Any]:
    """Agent-friendly summary for ec2_instances_by_tag responses.

    Pre-computes counts/ratios so the LLM can cite "web tier has 4 instances"
    without iterating arrays. Top engineering priority per the test-cases
    README: API responses must carry context (units, counts) for agents.
    """
    return {
        "by_tier_counts": {tier: len(ids) for tier, ids in by_tier.items()},
        "total_active": len(instances),
        "primary_tier": (max(by_tier, key=lambda t: len(by_tier[t])) if by_tier else ""),
        "vpcs_in_scope": sorted({i["vpc_id"] for i in instances if i.get("vpc_id")}),
        "azs_in_scope": sorted(
            {i["availability_zone"] for i in instances if i.get("availability_zone")}
        ),
    }


def build_elb_summary(
    target_groups: Sequence[Mapping[str, Any]],
    healthy_targets: Sequence[Mapping[str, Any]],
    unhealthy_targets: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    """Agent-friendly summary for get_elb_target_health responses."""
    total = len(healthy_targets) + len(unhealthy_targets)
    return {
        "total_targets": total,
        "healthy_count": len(healthy_targets),
        "unhealthy_count": len(unhealthy_targets),
        "healthy_ratio_pct": (round(100 * len(healthy_targets) / total, 1) if total else None),
        "unhealthy_states": sorted({t["state"] for t in unhealthy_targets if t.get("state")}),
        "target_group_count": len(target_groups),
    }


def extract_ec2_instances_params(sources: dict[str, dict]) -> dict[str, Any]:
    """Extract parameters for EC2 instance discovery by tag/tier.

    With a single tier hint we pass it as the ``tier`` filter. With multiple
    tiers (e.g. web + worker, the canonical multi-tier topology) we leave
    ``tier`` empty and rely on ``vpc_id`` / ``instance_ids`` so the tool
    returns every instance grouped by tier in ``by_tier`` — that grouping is
    what the planner needs to compare tiers side-by-side.
    """
    ec2 = sources.get("ec2")
    if ec2 is None:
        raise ValueError("Sources dictionary must contain an 'ec2' key with topology configuration")

    tiers = ec2.get("tiers") or []
    return {
        "tier": tiers[0] if len(tiers) == 1 else "",
        "instance_ids": list(ec2.get("instance_ids") or []),
        "vpc_id": ec2.get("vpc_id", ""),
        "region": ec2.get("region", "us-east-1"),
        "aws_backend": ec2.get("_backend"),
    }


def extract_target_health_params(sources: dict[str, dict]) -> dict[str, Any]:
    """Extract parameters for ELB target health queries.

    Passes the full ``target_group_arns`` list — multi-TG ALBs (one ALB →
    several listener rules → several target groups) are common, and silently
    truncating to the first ARN would hide whole tiers from the agent. The
    tool itself iterates the list. ``load_balancer_arn`` stays singular
    because the boto3 API takes one LB per call.
    """
    ec2 = sources.get("ec2")
    if ec2 is None:
        raise ValueError("Sources dictionary must contain an 'ec2' key with topology configuration")

    lb_arns = ec2.get("load_balancer_arns") or []
    return {
        "target_group_arns": list(ec2.get("target_group_arns") or []),
        "load_balancer_arn": lb_arns[0] if lb_arns else "",
        "region": ec2.get("region", "us-east-1"),
        "aws_backend": ec2.get("_backend"),
    }
