"""Mock AWS backend package for synthetic EC2/ELB tools.

Mirrors the structure of ``tests/synthetic/mock_eks_backend`` but serves
EC2/ELB topology evidence to the new ``ec2_instances_by_tag`` and
``get_elb_target_health`` tools.
"""

from tests.synthetic.mock_aws_backend.backend import AWSBackend, FixtureAWSBackend

__all__ = ["AWSBackend", "FixtureAWSBackend"]
