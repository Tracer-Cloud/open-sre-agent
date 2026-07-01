"""Fixtures for EC2 gateway deployment tests.

These tests require AWS credentials and TELEGRAM_BOT_TOKEN and should be skipped in CI.
Run manually with: pytest tests/deployment/ec2/ -v -s
"""

from __future__ import annotations

import os
from collections.abc import Generator
from typing import Any

import pytest

from tests.shared.infra import infrastructure_available


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

    os.environ.setdefault("OPENSRE_DEPLOY_MODE", "gateway")

    from infra.deploy.deploy import deploy
    from infra.deploy.destroy import destroy

    outputs = deploy()
    try:
        yield outputs
    finally:
        destroy()
