"""Fixtures for EC2 deployment tests (web + gateway on one instance).

These tests require AWS credentials and a chat gateway token
(Telegram and/or Slack) and should be skipped in CI.
Run manually with: pytest tests/deployment/ec2/ -v -s
"""

from __future__ import annotations

import os
from collections.abc import Generator
from typing import Any

import pytest

from tests.shared.infra import infrastructure_available


def _has_telegram_gateway() -> bool:
    return bool(os.getenv("TELEGRAM_BOT_TOKEN", "").strip())


def _has_slack_gateway() -> bool:
    return bool(os.getenv("SLACK_BOT_TOKEN", "").strip()) and bool(
        os.getenv("SLACK_APP_TOKEN", "").strip()
    )


@pytest.fixture(scope="session")
def gateway_deployment() -> Generator[dict[str, Any]]:
    """Deploy OpenSRE on EC2 (web + gateway), yield outputs, then terminate.

    Skips when:
    - Running in CI or SKIP_INFRA_TESTS is set (infrastructure gate), or
    - neither Telegram nor Slack Socket Mode tokens are set.
    """
    if not infrastructure_available():
        pytest.skip("Infrastructure tests skipped in CI — run manually")

    if not _has_telegram_gateway() and not _has_slack_gateway():
        pytest.skip(
            "Set TELEGRAM_BOT_TOKEN and/or SLACK_BOT_TOKEN+SLACK_APP_TOKEN "
            "before running gateway deployment tests"
        )

    from platform.deployment.ecr_deploy.lifecycle import deploy, destroy

    outputs = deploy()
    try:
        yield outputs
    finally:
        destroy()
