"""The shell builds via AgentBuildConfig without gateway-chat withholds."""

from __future__ import annotations

import io

from rich.console import Console

from gateway.core.host.capability_policy import ensure_gateway_capability_policy
from surfaces.interactive_shell.runtime.shell_agent import (
    build_shell_agent,
    shell_agent_build_config,
)
from surfaces.interactive_shell.session import Session


def test_shell_agent_build_omits_capability_policy() -> None:
    config = shell_agent_build_config()
    assert config.apply_capability_policy is None
    assert config.build_tools is not None
    assert config.build_prompts is not None
    assert config.build_gather is not None


def test_gateway_policy_still_withholds_on_a_fresh_session() -> None:
    session = Session()
    ensure_gateway_capability_policy(session)
    assert session.available_capabilities["investigation"] == ()
    assert session.available_capabilities["llm_provider"] == ()
    assert session.available_capabilities["task_cancel"] == ()


def test_build_shell_agent_keeps_investigation_capability() -> None:
    session = Session()
    session.available_capabilities["investigation"] = ("investigate",)
    build_shell_agent(session, Console(file=io.StringIO(), force_terminal=False))
    assert session.available_capabilities["investigation"] == ("investigate",)
