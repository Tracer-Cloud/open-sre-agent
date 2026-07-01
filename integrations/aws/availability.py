"""AWS-wide availability checks shared across AWS service tools.

The synthetic harnesses under ``tests/synthetic/`` inject a fixture
``_backend`` object via the integration source dict so AWS tools can
run against mocks. Helpers in this module accept either real
connection-verified credentials or a fixture backend.

This module is the canonical home for any availability helper used by
more than one AWS sub-service (currently EC2 and ELB share the
``ec2``/topology source check). Per-service-only helpers live in their
service's own ``integrations/<service>/availability.py``.
"""

from __future__ import annotations


def ec2_available_or_backend(sources: dict[str, dict]) -> bool:
    """Available when real EC2/AWS topology credentials are present OR a fixture backend is injected.

    Gates EC2/ELB tool wrappers whose ``extract_params`` can delegate to a
    mock ``aws_backend`` for synthetic tests. The ``ec2`` source is
    available when resolved integrations or synthetic backends provide
    EC2/ELB topology context.
    """
    ec2 = sources.get("ec2", {})
    return bool(ec2.get("connection_verified") or ec2.get("_backend"))
