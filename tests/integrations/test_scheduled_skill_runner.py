"""Tests for the scheduled recurring skill runner tool filter."""

from __future__ import annotations

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


def test_github_ci_health_skill_returns_complete_prefetched_report_without_agent_truncation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    skill_name, revision = pin_recurring_skill("github-ci-health")
    prefetched: list[tuple[str, dict[str, str]]] = []
    complete_report = "GitHub CI health — acme/api\n" + ("failure detail\n" * 100)

    def fake_prefetch(name: str, inputs: dict[str, str]) -> str:
        prefetched.append((name, inputs))
        return complete_report

    def fail_headless(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("deterministic CI reports must not pass through the action agent")

    monkeypatch.setattr(scheduled_skill_runner, "_prefetched_context", fake_prefetch)
    monkeypatch.setattr(AgentSession, "run_headless_turn", fail_headless)

    report = scheduled_skill_runner.run_scheduled_recurring_skill(
        {
            "skill_name": skill_name,
            "skill_revision": revision,
            "skill_inputs": {"owner": "acme", "repo": "api", "branch": "main"},
        }
    )

    assert len(report) > 512
    assert report == complete_report
    assert prefetched == [("github-ci-health", {"owner": "acme", "repo": "api", "branch": "main"})]
