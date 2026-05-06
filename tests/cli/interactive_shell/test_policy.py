"""Tests for ExecutionPolicyGate."""

from __future__ import annotations

from app.cli.interactive_shell.agent_actions import PlannedAction
from app.cli.interactive_shell.policy import ExecutionPolicyGate
from app.cli.interactive_shell.session import ReplSession


def test_policy_gate_allows_safe_actions() -> None:
    gate = ExecutionPolicyGate()
    session = ReplSession()

    # Slash command is allowed
    action = PlannedAction(kind="slash", content="/health", position=0)
    decision, reason = gate.evaluate(action, session)
    assert decision == "allow"
    assert "always allowed" in reason

    # LLM provider change is allowed
    action = PlannedAction(kind="llm_provider", content="anthropic", position=0)
    decision, reason = gate.evaluate(action, session)
    assert decision == "allow"
    assert "always allowed" in reason


def test_policy_gate_denies_destructive_commands() -> None:
    gate = ExecutionPolicyGate()
    session = ReplSession()

    # rm is denied
    action = PlannedAction(kind="shell", content="rm -rf /", position=0)
    decision, reason = gate.evaluate(action, session)
    assert decision == "deny"
    assert "potentially destructive" in reason

    # kill is denied
    action = PlannedAction(kind="shell", content="kill -9 123", position=0)
    decision, reason = gate.evaluate(action, session)
    assert decision == "deny"
    assert "potentially destructive" in reason


def test_policy_gate_asks_on_shell_command_outside_trust_mode() -> None:
    gate = ExecutionPolicyGate()
    session = ReplSession()
    assert session.trust_mode is False

    action = PlannedAction(kind="shell", content="pwd", position=0)
    decision, reason = gate.evaluate(action, session)
    assert decision == "ask"
    assert "outside of trust mode" in reason


def test_policy_gate_allows_shell_command_in_trust_mode() -> None:
    gate = ExecutionPolicyGate()
    session = ReplSession()
    session.trust_mode = True

    action = PlannedAction(kind="shell", content="pwd", position=0)
    decision, reason = gate.evaluate(action, session)
    assert decision == "allow"
    assert "trust mode" in reason
