"""Tests for the shared assistant three-tier response formatter."""

from __future__ import annotations

import pytest

from core.agent_harness.prompts.rules import (
    AGENT_RESPONSE_THREE_TIER_RULE,
    format_agent_response,
)


def test_format_agent_response_full_three_tier() -> None:
    text = format_agent_response(
        "CPU on `prod-api-3` has been above 90% for 45 minutes.",
        "Host: prod-api-3  CPU: 94%  Duration: 47m",
        "check recent deployments and memory pressure on that host?",
    )

    assert "**I found:**" in text
    assert "**Here's what that looks like:**" in text
    assert "**Want me to:**" in text
    assert "prod-api-3" in text


def test_format_agent_response_compact_single_line() -> None:
    text = format_agent_response("No integrations are configured in this session.")

    assert text == "No integrations are configured in this session."
    assert "**I found:**" not in text


def test_agent_response_rule_is_in_assistant_system_prompt() -> None:
    from core.agent_harness.prompts.assistant_agent_prompt import _build_system_prompt

    prompt = _build_system_prompt("ref", "history")

    assert AGENT_RESPONSE_THREE_TIER_RULE.split("\n", maxsplit=1)[0] in prompt
    assert "connecting another integration" in prompt


def test_format_agent_response_rejects_empty_found_with_detail() -> None:
    with pytest.raises(ValueError, match="found is required"):
        format_agent_response("", "Host: prod-api-3  CPU: 94%", "restart the pod?")


def test_observation_block_on_screen_requires_want_me_to() -> None:
    from core.agent_harness.prompts.assistant_agent_prompt import _build_observation_block

    block = _build_observation_block("grafana: passed", on_screen=True)

    assert "**Want me to:**" in block
    assert "connect another integration" in block
