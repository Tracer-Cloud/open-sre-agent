"""End-to-end test: deploy OpenSRE on EC2 (web + gateway) and verify both are alive.

Requires deployed infrastructure (see conftest.py / platform/deployment/deploy.py).
Run with: pytest tests/deployment/ec2/test_gateway_e2e.py -v -s

Prerequisites (local environment):
    TELEGRAM_BOT_TOKEN      - BotFather token (forwarded into the gateway container)
    TELEGRAM_ALLOWED_USERS  - comma-separated allowed user IDs
    LLM_PROVIDER + key      - so the agent can respond to messages
    AWS credentials         - for EC2/SSM/ECR provisioning
"""

from __future__ import annotations

import logging
import os
from typing import Any

import pytest
import requests

from platform.deployment.aws.ssm import run_ssm_shell_command
from platform.deployment.ecr_deploy.instance import (
    GATEWAY_CONTAINER_NAME,
    WEB_CONTAINER_NAME,
    poll_deployment_health,
)

logger = logging.getLogger(__name__)


@pytest.mark.e2e
class TestGatewayDeployment:
    """Validate that the gateway EC2 deployment lifecycle produces all required outputs."""

    def test_deploy_lifecycle(self, gateway_deployment: dict[str, Any]) -> None:
        """Verify the instance was provisioned with all required resources."""
        assert gateway_deployment["InstanceId"], "InstanceId missing"
        assert gateway_deployment["PublicIpAddress"], "PublicIpAddress missing"
        assert gateway_deployment["SecurityGroupId"], "SecurityGroupId missing"
        assert gateway_deployment["ProfileName"], "ProfileName missing"
        assert gateway_deployment["RoleName"], "RoleName missing"
        assert gateway_deployment["ImageUri"], "ImageUri missing"

        logger.info(
            "Gateway deployment lifecycle OK: instance=%s ip=%s image=%s",
            gateway_deployment["InstanceId"],
            gateway_deployment["PublicIpAddress"],
            gateway_deployment["ImageUri"],
        )


@pytest.mark.e2e
class TestWebHealth:
    """Validate that the web container health endpoint is reachable after deployment."""

    def test_web_health_endpoint(self, gateway_deployment: dict[str, Any]) -> None:
        """Verify the web container responds on port 8000."""
        public_ip = gateway_deployment["PublicIpAddress"]
        status = poll_deployment_health(f"http://{public_ip}:8000", max_attempts=12)
        assert status.status_code == 200
        logger.info("Web health OK at %s after %d attempts", status.url, status.attempts)

    def test_web_container_running(self, gateway_deployment: dict[str, Any]) -> None:
        """Verify the web container is running via SSM."""
        instance_id = gateway_deployment["InstanceId"]

        try:
            result = run_ssm_shell_command(
                instance_id=instance_id,
                commands=[
                    f"docker ps --filter name={WEB_CONTAINER_NAME} "
                    f"--filter status=running --format '{{{{.Names}}}}'",
                ],
            )
        except Exception as exc:
            pytest.skip(f"SSM Run Command unavailable: {exc}")
            return

        assert result["status"] == "Success"
        assert WEB_CONTAINER_NAME in result["stdout"]


@pytest.mark.e2e
class TestGatewayHealth:
    """Validate that the gateway process is alive and Telegram-reachable after deployment."""

    def test_gateway_process_running(self, gateway_deployment: dict[str, Any]) -> None:
        """Verify the gateway container is running and has logged the polling-started sentinel."""
        instance_id = gateway_deployment["InstanceId"]

        try:
            result = run_ssm_shell_command(
                instance_id=instance_id,
                commands=[
                    f"docker ps --filter name={GATEWAY_CONTAINER_NAME} "
                    f"--filter status=running --format '{{{{.Names}}}}'",
                    f"docker logs --tail 200 {GATEWAY_CONTAINER_NAME} 2>&1 || true",
                ],
            )
        except Exception as exc:
            pytest.skip(f"SSM Run Command unavailable: {exc}")
            return

        assert result["status"] == "Success", (
            f"SSM command failed (status={result['status']}): {result['stderr'][:300]}"
        )

        stdout = result["stdout"]

        # Container must appear in running list
        assert GATEWAY_CONTAINER_NAME in stdout, (
            f"Gateway container '{GATEWAY_CONTAINER_NAME}' not running. SSM stdout:\n{stdout[:500]}"
        )

        # Gateway must have logged the startup sentinel
        assert "polling started" in stdout, (
            f"Gateway polling-started sentinel not found in container logs. "
            f"SSM stdout:\n{stdout[:1000]}"
        )

        logger.info("Gateway process health check OK on instance %s", instance_id)

    def test_telegram_bot_reachable(self, gateway_deployment: dict[str, Any]) -> None:
        """Verify the Telegram bot token is valid and the bot is reachable via getMe."""
        instance_id = gateway_deployment["InstanceId"]
        bot_token = os.getenv("TELEGRAM_BOT_TOKEN")
        if not bot_token:
            pytest.skip("TELEGRAM_BOT_TOKEN not set — cannot verify Telegram reachability")
            return

        url = f"https://api.telegram.org/bot{bot_token}/getMe"

        try:
            resp = requests.get(url, timeout=15)
        except requests.exceptions.RequestException as exc:
            pytest.skip(f"Telegram API unreachable: {exc}")
            return

        assert resp.status_code == 200, (
            f"Telegram getMe returned {resp.status_code}: {resp.text[:300]}"
        )

        payload = resp.json()
        assert payload.get("ok") is True, f"Telegram getMe ok=false: {payload}"
        assert payload.get("result", {}).get("username"), (
            "Telegram getMe response missing bot username"
        )

        logger.info(
            "Telegram bot reachable: @%s (id=%s, gateway_instance=%s)",
            payload["result"]["username"],
            payload["result"].get("id"),
            instance_id,
        )
