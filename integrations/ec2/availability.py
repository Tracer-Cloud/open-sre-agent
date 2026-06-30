"""Backend-aware availability check for EC2/ELB tools.

The synthetic harnesses under ``tests/synthetic/`` inject a fixture
``_backend`` object via the integration source dict so tools can run
against mocks. This helper accepts either real connection-verified
credentials or a fixture backend, so vendor tools share one consistent
availability check.
"""

from __future__ import annotations


def ec2_available_or_backend(sources: dict[str, dict]) -> bool:
    """Available when real EC2/AWS credentials are present OR a fixture backend is injected.

    Mirrors ``eks_available_or_backend``: gates EC2/ELB tool wrappers whose
    ``extract_params`` can delegate to a mock ``aws_backend`` for synthetic tests.
    The ``ec2`` source is available when resolved integrations or synthetic
    backends provide EC2/ELB topology context.
    """
    ec2 = sources.get("ec2", {})
    return bool(ec2.get("connection_verified") or ec2.get("_backend"))
