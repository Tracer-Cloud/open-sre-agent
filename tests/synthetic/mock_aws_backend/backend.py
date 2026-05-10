"""AWSBackend Protocol and FixtureAWSBackend for synthetic EC2/ELB testing.

The Protocol defines the minimal surface the EC2/RDS investigation agent
uses to query EC2/ELB topology state. FixtureAWSBackend satisfies it by
serving scenario fixture data in the exact shape the tools under
``app/tools/EC2InstancesByTagTool/`` and ``app/tools/ELBTargetHealthTool/``
return — no boto3 calls, no AWS credentials required.

Usage
-----
    resolved_integrations = {
        "aws": {
            "region": "us-east-1",
            "ec2_backend": FixtureAWSBackend(fixture),
        }
    }

The detect_sources block reads ``ec2_backend`` and exposes it on
``available_sources["ec2"]["_backend"]`` (and ``available_sources["rds"]``)
without colliding with the EKS slot ``available_sources["eks"]["_backend"]``.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable

from app.tools.utils.aws_topology_helper import build_ec2_summary, build_elb_summary

if TYPE_CHECKING:
    from tests.synthetic.rds_postgres.scenario_loader import ScenarioFixture


@runtime_checkable
class AWSBackend(Protocol):
    """Minimal AWS interface used by the EC2/RDS investigation agent."""

    def describe_instances_by_tag(
        self,
        tier: str = "",
        instance_ids: list[str] | None = None,
        vpc_id: str = "",
        **kwargs: Any,
    ) -> dict[str, Any]:
        """Return a response matching ``ec2_instances_by_tag``."""

    def describe_target_health(
        self,
        target_group_arns: list[str] | None = None,
        target_group_arn: str = "",
        load_balancer_arn: str = "",
        **kwargs: Any,
    ) -> dict[str, Any]:
        """Return a response matching ``get_elb_target_health``."""


class FixtureAWSBackend:
    """AWSBackend implementation backed by a ScenarioFixture.

    Each method wraps the corresponding fixture file in the envelope that the
    real tool function returns. Calling a method for an evidence source that
    the scenario did not declare in ``available_evidence`` raises ValueError.
    """

    def __init__(self, fixture: ScenarioFixture) -> None:
        self._fixture = fixture

    # ------------------------------------------------------------------ EC2

    def describe_instances_by_tag(
        self,
        tier: str = "",
        instance_ids: list[str] | None = None,
        vpc_id: str = "",
        **_: Any,
    ) -> dict[str, Any]:
        ec2_fixture = self._fixture.evidence.ec2_instances_by_tag
        if ec2_fixture is None:
            raise ValueError(
                f"{self._fixture.scenario_id}: describe_instances_by_tag called but "
                "'ec2_instances_by_tag' is not declared in available_evidence"
            )

        all_instances = list(ec2_fixture.get("instances", []))
        instances = all_instances
        if instance_ids:
            id_set = set(instance_ids)
            instances = [i for i in all_instances if i.get("instance_id", "") in id_set]
        elif tier:
            instances = [i for i in all_instances if (i.get("tier", "") or "") == tier]
        if vpc_id:
            # Strict equality — real EC2 API never returns instances from other
            # VPCs, and missing/empty vpc_id is a fixture error worth surfacing.
            instances = [i for i in instances if i.get("vpc_id", "") == vpc_id]

        by_tier: dict[str, list[str]] = {}
        for inst in instances:
            bucket = by_tier.setdefault(inst.get("tier") or "untagged", [])
            iid = inst.get("instance_id") or ""
            if iid:
                bucket.append(iid)

        return {
            "source": "ec2",
            "available": True,
            "tier": tier,
            "vpc_id": vpc_id,
            "total_instances": len(instances),
            "instances": instances,
            "by_tier": by_tier,
            "tiers_detected": sorted(by_tier.keys()),
            "summary": build_ec2_summary(instances, by_tier),
            "error": None,
        }

    # ------------------------------------------------------------------ ELB

    def describe_target_health(
        self,
        target_group_arns: list[str] | None = None,
        target_group_arn: str = "",
        load_balancer_arn: str = "",
        **_: Any,
    ) -> dict[str, Any]:
        elb_fixture = self._fixture.evidence.elb_target_health
        if elb_fixture is None:
            raise ValueError(
                f"{self._fixture.scenario_id}: describe_target_health called but "
                "'elb_target_health' is not declared in available_evidence"
            )

        target_groups = list(elb_fixture.get("target_groups", []))
        targets = list(elb_fixture.get("targets", []))

        # Accept the canonical plural list or the singular convenience alias.
        arns = set(target_group_arns or [])
        if target_group_arn:
            arns.add(target_group_arn)

        if arns:
            target_groups = [tg for tg in target_groups if tg.get("TargetGroupArn") in arns]
            targets = [t for t in targets if t.get("target_group_arn") in arns]
        elif load_balancer_arn:
            target_groups = [
                tg
                for tg in target_groups
                if load_balancer_arn in (tg.get("LoadBalancerArns") or [])
            ]
            allowed_arns = {tg.get("TargetGroupArn", "") for tg in target_groups}
            targets = [t for t in targets if t.get("target_group_arn", "") in allowed_arns]

        healthy = [t for t in targets if t.get("state") == "healthy"]
        unhealthy = [t for t in targets if t.get("state") != "healthy"]
        instance_ids = [t.get("instance_id", "") for t in targets if t.get("instance_id")]

        return {
            "source": "ec2",
            "available": True,
            "target_groups": target_groups,
            "healthy_targets": healthy,
            "unhealthy_targets": unhealthy,
            "instance_ids": list(dict.fromkeys(instance_ids)),
            "summary": build_elb_summary(target_groups, healthy, unhealthy),
            "error": None,
        }
