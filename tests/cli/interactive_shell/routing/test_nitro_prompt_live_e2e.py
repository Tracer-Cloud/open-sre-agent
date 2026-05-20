"""Live AWS + LLM end-to-end validation for the Nitro deploy/investigate prompt."""

from __future__ import annotations

import io
import os
import uuid

import pytest
from rich.console import Console

from app.cli.interactive_shell.orchestration.action_planner import plan_actions_with_unhandled
from app.cli.interactive_shell.orchestration.agent_actions import execute_cli_actions
from app.cli.interactive_shell.routing.router import route_input
from app.cli.interactive_shell.runtime.session import ReplSession
from app.deployment.methods.ec2_remote import destroy_remote
from app.deployment.operations.ec2_config import load_remote_outputs
from app.deployment.operations.health import poll_deployment_health
from app.remote.client import RemoteAgentClient
from app.utils.config import get_env
from tests.shared.infra import infrastructure_available

pytestmark = [pytest.mark.integration, pytest.mark.live_llm, pytest.mark.live_aws]

_NITRO_PROMPT = (
    "I want to deploy OpenSRE on a remote EC2 Nitro instance, and then I want to send\n"
    'it an investigation. Can you please deploy the instance and send it "hello world"?'
)


def _require_live_env() -> None:
    if not infrastructure_available():
        pytest.skip("Live AWS infrastructure is disabled in this environment.")
    if not os.getenv("ANTHROPIC_API_KEY"):
        pytest.skip("ANTHROPIC_API_KEY is required for live LLM routing.")
    if not get_env("OPENSRE_API_KEY"):
        pytest.skip("OPENSRE_API_KEY is required for remote server authentication.")


def test_nitro_prompt_live_e2e_deploys_and_sends_remote_alert(monkeypatch: pytest.MonkeyPatch) -> None:
    _require_live_env()

    stack_name = f"tracer-ec2-remote-e2e-{uuid.uuid4().hex[:8]}"
    monkeypatch.setenv("OPENSRE_REMOTE_STACK_NAME", stack_name)

    session = ReplSession()
    console = Console(file=io.StringIO(), force_terminal=False, highlight=False)

    try:
        decision = route_input(_NITRO_PROMPT, session)
        assert decision.route_kind.value == "cli_agent"
        assert not decision.fallback_reason

        actions, has_unhandled_clause = plan_actions_with_unhandled(_NITRO_PROMPT)
        assert has_unhandled_clause is False
        assert [(action.kind, action.content) for action in actions] == [
            ("remote_deploy", "ec2_remote"),
            ("remote_investigation", "hello world"),
        ]

        handled = execute_cli_actions(
            _NITRO_PROMPT,
            session,
            console,
            confirm_fn=lambda _prompt: "y",
            is_tty=True,
        )
        assert handled is True

        outputs = load_remote_outputs()
        host = str(outputs.get("PublicIpAddress", "")).strip()
        port = str(outputs.get("ServerPort", "8080")).strip() or "8080"
        assert host
        assert port == "8080"

        base_url = f"http://{host}:{port}"
        poll_deployment_health(base_url, interval_seconds=10.0, max_attempts=30)

        remote_client = RemoteAgentClient(base_url=base_url, api_key=get_env("OPENSRE_API_KEY"))
        investigations = remote_client.list_investigations()
        assert investigations
        assert any("interactive-shell-remote-investigation" in item["id"] for item in investigations)

        assert {"type": "remote_deploy", "text": "ec2_remote", "ok": True} in session.history
        assert {"type": "remote_alert", "text": "hello world", "ok": True} in session.history
    finally:
        destroy_remote(stack_name=stack_name)
