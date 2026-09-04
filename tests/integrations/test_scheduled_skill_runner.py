"""Tests for the scheduled recurring skill runner tool filter."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from core.agent_harness import AgentSession, pin_recurring_skill
from core.agent_harness.tools.tool_provider import tool_allowed_for_unattended_run
from core.tool import SideEffectLevel
from integrations import scheduled_skill_runner


class _FakeTool:
    def __init__(self, name: str, level: SideEffectLevel | None) -> None:
        self.name = name
        self.side_effect_level = level


def test_unattended_run_allows_read_only_tools_only() -> None:
    assert (
        tool_allowed_for_unattended_run(_FakeTool("shell_run", SideEffectLevel.MUTATING)) is False
    )
    assert (
        tool_allowed_for_unattended_run(_FakeTool("slack_read_messages", SideEffectLevel.READ_ONLY))
        is True
    )
    assert (
        tool_allowed_for_unattended_run(_FakeTool("slack_add_reaction", SideEffectLevel.EXTERNAL))
        is False
    )
    assert (
        tool_allowed_for_unattended_run(_FakeTool("slack_send_message", SideEffectLevel.EXTERNAL))
        is False
    )
    assert (
        tool_allowed_for_unattended_run(
            _FakeTool("execute_github_issue_mutation", SideEffectLevel.MUTATING)
        )
        is False
    )
    assert (
        tool_allowed_for_unattended_run(_FakeTool("fix_github_pr_ci", SideEffectLevel.MUTATING))
        is False
    )
    assert (
        tool_allowed_for_unattended_run(_FakeTool("cli_command", SideEffectLevel.MUTATING)) is False
    )
    assert (
        tool_allowed_for_unattended_run(
            _FakeTool("propose_scheduled_delivery", SideEffectLevel.MUTATING)
        )
        is False
    )
    assert tool_allowed_for_unattended_run(_FakeTool("undeclared", None)) is False
    assert (
        tool_allowed_for_unattended_run(_FakeTool("execute_python_code", SideEffectLevel.READ_ONLY))
        is False
    )


def test_github_ci_health_skill_is_resolved_prefetched_and_run_unattended(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    skill_name, revision = pin_recurring_skill("github-ci-health")
    prefetched: list[tuple[str, dict[str, str]]] = []
    headless_calls: list[tuple[str, dict[str, object]]] = []

    def fake_prefetch(name: str, inputs: dict[str, str]) -> str:
        prefetched.append((name, inputs))
        return "GitHub CI health — acme/api — branch main\nNo failing checks found."

    def fake_headless(message: str, **kwargs: object) -> SimpleNamespace:
        headless_calls.append((message, kwargs))
        return SimpleNamespace(answered=True, primary_response_text="final CI report")

    monkeypatch.setattr(scheduled_skill_runner, "_prefetched_context", fake_prefetch)
    monkeypatch.setattr(AgentSession, "run_headless_turn", fake_headless)

    report = scheduled_skill_runner.run_scheduled_recurring_skill(
        {
            "skill_name": skill_name,
            "skill_revision": revision,
            "skill_inputs": {"owner": "acme", "repo": "api", "branch": "main"},
        }
    )

    assert report == "final CI report"
    assert prefetched == [("github-ci-health", {"owner": "acme", "repo": "api", "branch": "main"})]
    message, kwargs = headless_calls[0]
    assert "Skill: github-ci-health" in message
    assert "GitHub CI health — acme/api" in message
    assert "Never call `fix_github_pr_ci`" in message
    assert kwargs["unattended"] is True
