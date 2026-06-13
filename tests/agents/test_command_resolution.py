"""Tests for /agents command resolution and completion logic."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest
from prompt_toolkit.completion import CompleteEvent
from prompt_toolkit.document import Document

from app.agents.registry import AgentRecord, AgentRegistry, resolve_agent_arg
from app.cli.interactive_shell.prompting.prompt_surface import ShellCompleter


@pytest.fixture
def temp_registry_path(tmp_path: Path) -> Path:
    return tmp_path / "agents.jsonl"


@pytest.fixture
def populated_registry(temp_registry_path: Path) -> AgentRegistry:
    registry = AgentRegistry(path=temp_registry_path)

    # Register some agents
    agent1 = AgentRecord(name="agent-alpha", pid=1001, command="python alpha.py")
    agent2 = AgentRecord(name="agent-beta", pid=1002, command="python beta.py")
    agent3 = AgentRecord(name="agent-alpha", pid=1003, command="python alpha2.py")  # Ambiguous name

    registry.register(agent1)
    registry.register(agent2)
    registry.register(agent3)
    return registry


def test_resolve_agent_arg_direct_pid_hit(populated_registry: AgentRegistry) -> None:
    # PID exists in registry
    pid = resolve_agent_arg("1001", populated_registry)
    assert pid == 1001


def test_resolve_agent_arg_unique_name_hit(populated_registry: AgentRegistry) -> None:
    # Name exists uniquely
    pid = resolve_agent_arg("agent-beta", populated_registry)
    assert pid == 1002


def test_resolve_agent_arg_direct_pid_fallback(populated_registry: AgentRegistry) -> None:
    # PID doesn't exist in registry, but parses as positive int
    pid = resolve_agent_arg("9999", populated_registry)
    assert pid == 9999


def test_resolve_agent_arg_ambiguous_name(populated_registry: AgentRegistry) -> None:
    # Name matches multiple records
    with pytest.raises(ValueError) as excinfo:
        resolve_agent_arg("agent-alpha", populated_registry)
    assert "ambiguous" in str(excinfo.value)
    assert "1001, 1003" in str(excinfo.value)


def test_resolve_agent_arg_invalid(populated_registry: AgentRegistry) -> None:
    # Completely unknown name
    with pytest.raises(ValueError) as excinfo:
        resolve_agent_arg("nonexistent-agent", populated_registry)
    assert "invalid pid or unknown agent name" in str(excinfo.value)

    # Invalid int format (negative/zero)
    with pytest.raises(ValueError) as excinfo:
        resolve_agent_arg("-5", populated_registry)
    assert "invalid pid or unknown agent name" in str(excinfo.value)


def test_shell_completer_agents_subcommands(populated_registry: AgentRegistry) -> None:
    completer = ShellCompleter()

    # Mock the AgentRegistry inside get_completions by patching its definition module
    with patch("app.agents.registry.AgentRegistry", return_value=populated_registry):
        # 1. Completions when user typed "/agents kill "
        doc = Document("/agents kill ", cursor_position=13)
        completions = list(completer.get_completions(doc, CompleteEvent()))

        # Should complete both names and PIDs
        texts = [c.text for c in completions]
        assert "agent-alpha" in texts
        assert "agent-beta" in texts
        assert "1001" in texts
        assert "1002" in texts
        assert "1003" in texts

        # 2. Completions when user typed "/agents kill agent-a"
        doc_partial = Document("/agents kill agent-a", cursor_position=20)
        completions_partial = list(completer.get_completions(doc_partial, CompleteEvent()))
        texts_partial = [c.text for c in completions_partial]
        assert "agent-alpha" in texts_partial
        assert "agent-beta" not in texts_partial
