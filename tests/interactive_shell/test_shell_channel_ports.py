"""The shell is a channel: ChannelAgentPorts without gateway-chat withholds."""

from __future__ import annotations

import io

from rich.console import Console

from gateway.core.host.capability_policy import ensure_gateway_capability_policy
from surfaces.interactive_shell.runtime.shell_agent import (
    build_shell_agent,
    preserve_host_capabilities,
    shell_channel_ports,
)
from surfaces.interactive_shell.session import Session


def test_shell_channel_ports_do_not_use_gateway_chat_policy() -> None:
    ports = shell_channel_ports()
    assert ports.surface == "interactive_shell"
    assert ports.apply_capability_policy is preserve_host_capabilities
    assert ports.apply_capability_policy is not ensure_gateway_capability_policy


def test_shell_policy_does_not_withhold_investigation_tools() -> None:
    session = Session()
    session.available_capabilities["investigation"] = ("investigate",)
    session.available_capabilities["llm_provider"] = ("set_model",)
    session.available_capabilities["task_cancel"] = ("cancel_task",)
    policy = shell_channel_ports().apply_capability_policy
    assert policy is preserve_host_capabilities
    policy(session)
    assert session.available_capabilities["investigation"] == ("investigate",)
    assert session.available_capabilities["llm_provider"] == ("set_model",)
    assert session.available_capabilities["task_cancel"] == ("cancel_task",)


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
