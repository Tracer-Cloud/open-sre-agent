"""Tests for scheduled manual loop runner prompts."""

from __future__ import annotations

import pytest

from integrations.manual_loop_runner import build_manual_loop_prompt


def test_build_manual_loop_prompt_constrains_output_to_requested_report() -> None:
    prompt = build_manual_loop_prompt(
        {
            "name": "opensre-star-report-minutely",
            "loop_prompt": (
                "Fetch the day-by-day star history for Tracer-Cloud/opensre. "
                "Send the report to Telegram."
            ),
        }
    )

    assert "Produce only the report body requested below" in prompt
    assert "Do not start an RCA" in prompt
    assert "Do not send, post, notify, or message any channel" in prompt
    assert "get_github_star_history" in prompt
    assert "opensre-star-report-minutely" in prompt
    assert "Fetch the day-by-day star history" in prompt


def test_build_manual_loop_prompt_rejects_empty_prompt() -> None:
    with pytest.raises(RuntimeError, match="prompt is empty"):
        build_manual_loop_prompt({"name": "empty"})
