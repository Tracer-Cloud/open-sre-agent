"""Fixtures for EC2 deployment tests (web + gateway on one instance).

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
    """Deploy OpenSRE on EC2 (web + gateway), yield outputs, then terminate.

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

    from platform.deployment.lifecycle import deploy, destroy

    outputs = deploy()
    try:
        yield outputs
    finally:
        destroy()
