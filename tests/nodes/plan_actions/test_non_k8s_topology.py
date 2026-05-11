"""Regression tests for the non-Kubernetes EC2/RDS topology contract.

These tests guard the promise of issue #1440: an investigation that does not
carry ``kube_*`` annotations must not error, must populate the EC2/RDS
topology sources, and must not silently activate the EKS code path. The
inverse (a Kubernetes alert) must continue to populate the EKS source and
leave the EC2/RDS keys absent.
"""

from __future__ import annotations

from typing import Any

from app.nodes.plan_actions.detect_sources import detect_sources
from tests.synthetic.mock_aws_backend import AWSBackend


def _ec2_rds_alert() -> dict[str, Any]:
    """Build a synthetic alert that carries only EC2/RDS topology hints."""
    return {
        "alert_source": "cloudwatch",
        "commonAnnotations": {
            "summary": "DBConnections climbing on orders-prod",
            "db_instance_identifier": "orders-prod",
            "engine": "mysql",
            "aws_region": "us-east-1",
            "vpc_id": "vpc-0a1b2c3d",
            "target_group_arn": (
                "arn:aws:elasticloadbalancing:us-east-1:111122223333:"
                "targetgroup/orders-web-tg/def456"
            ),
            "load_balancer_arn": (
                "arn:aws:elasticloadbalancing:us-east-1:111122223333:"
                "loadbalancer/app/orders-alb/abc123"
            ),
            "auto_scaling_group": "orders-web-asg",
            "tier": "web",
        },
    }


def _eks_alert() -> dict[str, Any]:
    """Build a synthetic alert that carries only EKS topology hints."""
    return {
        "alert_source": "cloudwatch",
        "commonAnnotations": {
            "summary": "Pod CrashLoopBackOff in payments namespace",
            "eks_cluster": "payments-eks",
            "kube_namespace": "payments",
            "pod_name": "checkout-7d8c-abc",
            "kube_deployment": "checkout",
            "aws_region": "us-east-1",
        },
    }


class _StubAWSBackend:
    """Minimal AWSBackend stub for the credential-gate bypass test.

    Implements the full AWSBackend Protocol surface (EC2, ELB, and RDS)
    because the synthetic suite routes RDS describe calls through this same
    backend slot. Returning ``available=False`` for RDS is sufficient — this
    test only cares that ``isinstance(stub, AWSBackend)`` holds, not about
    the response shape.
    """

    def describe_instances_by_tag(self, **_: Any) -> dict[str, Any]:
        return {"source": "ec2", "available": True, "instances": [], "error": None}

    def describe_target_health(self, **_: Any) -> dict[str, Any]:
        return {"source": "ec2", "available": True, "target_groups": [], "error": None}

    def describe_db_instances(self, **_: Any) -> dict[str, Any]:
        return {"source": "rds", "available": False, "error": None}

    def describe_db_events(self, **_: Any) -> dict[str, Any]:
        return {"source": "rds", "available": False, "events": [], "error": None}


def _aws_credentialed_integrations(*, with_ec2_backend: bool = False) -> dict[str, Any]:
    """Minimal resolved_integrations dict that satisfies the AWS credential gate.

    ``role_arn`` is the simplest gate; ``ec2_backend`` simulates a fixture
    injection (FixtureAWSBackend in the synthetic suite).
    """
    aws: dict[str, Any] = {"region": "us-east-1", "role_arn": "arn:aws:iam::111122223333:role/sre"}
    if with_ec2_backend:
        backend = _StubAWSBackend()
        # Sanity-check the stub satisfies the runtime Protocol contract so that
        # a future shape drift is caught here rather than in fixture replays.
        assert isinstance(backend, AWSBackend)
        aws["ec2_backend"] = backend
    return {"aws": aws}


def test_no_kube_metadata_populates_ec2_and_rds_sources() -> None:
    sources = detect_sources(
        _ec2_rds_alert(),
        context={},
        resolved_integrations=_aws_credentialed_integrations(),
    )

    assert "ec2" in sources, "ec2 topology source must be present for non-K8s alerts"
    assert "rds" in sources, (
        "rds topology source must be present when db_instance_identifier is set"
    )
    assert "eks" not in sources, "eks source must not be silently activated"

    ec2 = sources["ec2"]
    assert ec2["region"] == "us-east-1"
    assert ec2["vpc_id"] == "vpc-0a1b2c3d"
    assert ec2["tiers"] == ["web"]
    assert ec2["target_group_arns"], "target_group_arn must be captured"
    assert ec2["load_balancer_arns"], "load_balancer_arn must be captured"
    assert ec2["auto_scaling_groups"] == ["orders-web-asg"]
    assert ec2["connection_verified"] is True

    rds = sources["rds"]
    assert rds["db_instance_identifier"] == "orders-prod"
    assert rds["engine"] == "mysql"
    assert rds["region"] == "us-east-1"
    assert rds["topology"]["consumer_tiers"] == ["web"]
    assert rds["connection_verified"] is True


def test_no_kube_metadata_does_not_error_without_credentials() -> None:
    """Without AWS credentials the EC2/RDS block must skip gracefully (no exception)."""
    sources = detect_sources(
        _ec2_rds_alert(),
        context={},
        resolved_integrations=None,
    )
    assert "ec2" not in sources
    assert "rds" not in sources
    assert "eks" not in sources


def test_ec2_backend_injection_bypasses_credential_gate() -> None:
    """A fixture-style ec2_backend must satisfy the AWS gate exactly like role_arn does."""
    sources = detect_sources(
        _ec2_rds_alert(),
        context={},
        resolved_integrations=_aws_credentialed_integrations(with_ec2_backend=True),
    )
    assert "ec2" in sources
    assert sources["ec2"].get("_backend") is not None
    assert "connection_verified" not in sources["ec2"], (
        "Backend-injected path must not set connection_verified — "
        "it would activate real-AWS tools alongside the fixture."
    )


def test_kube_metadata_still_populates_eks_source() -> None:
    """Counter-test: K8s alerts must continue to write the EKS source unchanged."""
    sources = detect_sources(
        _eks_alert(),
        context={},
        resolved_integrations=_aws_credentialed_integrations(),
    )
    assert "eks" in sources, "EKS source must remain populated for K8s alerts"
    assert sources["eks"]["cluster_name"] == "payments-eks"
    assert sources["eks"]["namespace"] == "payments"
    # No EC2/RDS topology hints in this alert → keys should be absent.
    assert "ec2" not in sources
    assert "rds" not in sources


def test_legacy_rds_integration_does_not_clobber_topology() -> None:
    """The legacy ``rds`` integration block must merge, not overwrite, the
    topology source written by the EC2/RDS block. Without this guard,
    ``topology``, ``_backend`` and ``connection_verified`` silently disappear
    when both an alert and a configured rds integration are present.
    """
    integrations = _aws_credentialed_integrations()
    integrations["rds"] = {"db_instance_identifier": "orders-prod", "region": "us-east-1"}

    sources = detect_sources(
        _ec2_rds_alert(),
        context={},
        resolved_integrations=integrations,
    )
    rds = sources["rds"]
    assert rds["db_instance_identifier"] == "orders-prod"
    assert rds["region"] == "us-east-1"
    assert "topology" in rds, "topology block must survive the legacy rds_int merge"
    assert rds["topology"]["consumer_tiers"] == ["web"]
    assert rds.get("connection_verified") is True


def test_tier_uses_exact_match_not_substring() -> None:
    """Annotations like 'frontier_count' or 'multi_tier_app' must NOT be
    treated as a tier hint — only the exact key 'tier' counts. Substring
    matching on a 4-letter token would otherwise pollute aws_metadata and
    silently activate the EC2 topology block on unrelated alerts.
    """
    alert: dict[str, Any] = {
        "alert_source": "cloudwatch",
        "commonAnnotations": {
            "frontier_count": "5",
            "multi_tier_app": "true",
            "tier_count": "3",
            "db_instance_identifier": "orders-prod",
            "aws_region": "us-east-1",
        },
    }
    sources = detect_sources(
        alert,
        context={},
        resolved_integrations=_aws_credentialed_integrations(),
    )
    # ec2 topology must NOT activate — there are no real EC2/ELB hints.
    assert "ec2" not in sources
    # rds source still populates from db_instance_identifier, but consumer_tiers
    # must be empty since no exact 'tier' annotation was set.
    assert sources["rds"]["topology"]["consumer_tiers"] == []


def test_exact_tier_annotation_still_captured() -> None:
    """Counter-test: an exact 'tier' key still flows through to ec2.tiers."""
    sources = detect_sources(
        _ec2_rds_alert(),
        context={},
        resolved_integrations=_aws_credentialed_integrations(),
    )
    assert sources["ec2"]["tiers"] == ["web"]


def test_vpc_id_alone_activates_ec2_topology() -> None:
    """A minimal RDS alert that only carries db_instance_identifier + vpc_id
    must still activate the EC2 topology source so the agent can enumerate
    EC2 instances in the same VPC. Without this, the only way to drive the
    EC2 path was to pre-populate tier / TG / LB / ASG in the annotations,
    which made the synthetic scenario "tell the agent the answer".
    """
    alert: dict[str, Any] = {
        "alert_source": "cloudwatch",
        "commonAnnotations": {
            "summary": "DBConnections climbing on orders-prod",
            "db_instance_identifier": "orders-prod",
            "engine": "mysql",
            "aws_region": "us-east-1",
            "vpc_id": "vpc-0a1b2c3d",
        },
    }
    sources = detect_sources(
        alert,
        context={},
        resolved_integrations=_aws_credentialed_integrations(),
    )
    assert "ec2" in sources
    assert sources["ec2"]["vpc_id"] == "vpc-0a1b2c3d"
    # No tier/TG/LB/ASG were given — those fields stay empty so the agent
    # has to discover them via the new tools.
    assert sources["ec2"]["tiers"] == []
    assert sources["ec2"]["target_group_arns"] == []
    assert sources["ec2"]["load_balancer_arns"] == []
    assert sources["ec2"]["auto_scaling_groups"] == []


def test_hybrid_alert_populates_both_eks_and_ec2() -> None:
    """An alert carrying both K8s and EC2/RDS hints should populate both sources."""
    alert = _eks_alert()
    alert["commonAnnotations"].update(
        {
            "db_instance_identifier": "orders-prod",
            "tier": "web",
        }
    )
    sources = detect_sources(
        alert,
        context={},
        resolved_integrations=_aws_credentialed_integrations(),
    )
    assert "eks" in sources
    assert "ec2" in sources
    assert "rds" in sources
