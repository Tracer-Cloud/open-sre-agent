"""Fixtures for EC2 deployment test cases.

These tests require AWS credentials with EC2 access and should be skipped in CI.
Run manually with: pytest tests/deployment/ec2/ -v -s
"""

from __future__ import annotations

import os
from collections.abc import Generator
from typing import Any

import pytest

from tests.shared.infra import infrastructure_available


@pytest.fixture(scope="session")
def ec2_deployment() -> Generator[dict[str, Any]]:
    """Deploy OpenSRE on EC2, yield outputs, then terminate.

    Skips when running in CI or when SKIP_INFRA_TESTS is set.
    """
    if not infrastructure_available():
        pytest.skip("Infrastructure tests skipped in CI — run manually")

    from tests.deployment.ec2.infrastructure_sdk.deploy import deploy
    from tests.deployment.ec2.infrastructure_sdk.destroy import destroy

    outputs = deploy()
    try:
        yield outputs
    finally:
        destroy()


@pytest.fixture(scope="session")
def gateway_deployment() -> Generator[dict[str, Any]]:
    """Deploy the Telegram Gateway on EC2, yield outputs, then terminate.

    Skips when:
    - Running in CI or SKIP_INFRA_TESTS is set (infrastructure gate), or
    - TELEGRAM_BOT_TOKEN is not set (required for container and getMe check).
    """
    if not infrastructure_available():
        pytest.skip("Infrastructure tests skipped in CI — run manually")

    if not os.getenv("TELEGRAM_BOT_TOKEN"):
        pytest.skip(
            "TELEGRAM_BOT_TOKEN is not set — export it before running gateway deployment tests"
        )

    from infra.deploy_gateway.deploy import deploy
    from infra.deploy_gateway.destroy import destroy

    outputs = deploy()
    try:
        yield outputs
    finally:
        destroy()
