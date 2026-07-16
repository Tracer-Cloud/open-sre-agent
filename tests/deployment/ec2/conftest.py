"""Fixtures for EC2 deployment tests (web + gateway on one instance).

These tests require AWS credentials and a Telegram bot token and should be
skipped in CI. The EC2 deploy is Telegram-only — Slack deploys via Terraform
(a separate Terraform module). Run manually with:
pytest tests/deployment/ec2/ -v -s
"""

from __future__ import annotations

import os
from collections.abc import Generator
from typing import Any

import pytest

from tests.shared.infra import infrastructure_available


def _has_telegram_gateway() -> bool:
    return bool(os.getenv("TELEGRAM_BOT_TOKEN", "").strip())


@pytest.fixture(scope="session")
def gateway_deployment() -> Generator[dict[str, Any]]:
    """Deploy OpenSRE on EC2 (web + gateway), yield outputs, then terminate.

    Skips when:
    - Running in CI or SKIP_INFRA_TESTS is set (infrastructure gate), or
    - no Telegram bot token is set (the EC2 gateway is Telegram-only).
    """
    if not infrastructure_available():
        pytest.skip("Infrastructure tests skipped in CI — run manually")

    if not _has_telegram_gateway():
        pytest.skip("Set TELEGRAM_BOT_TOKEN before running gateway deployment tests")

    from platform.deployment.ecr_deploy.lifecycle import deploy, destroy

    outputs = deploy()
    try:
        yield outputs
    finally:
        destroy()
