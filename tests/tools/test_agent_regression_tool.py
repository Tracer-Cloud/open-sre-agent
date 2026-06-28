"""Unit tests for tools/agent_regression_tool.py."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import yaml

from tools.agent_regression_tool import (
    create_agent_regression_scenario,
    summarize_agent_test_status,
)


class FakeProcess:
    def __init__(self, pid: int, name: str, cmdline: list[str]) -> None:
        self.info = {
            "pid": pid,
            "name": name,
            "cmdline": cmdline,
        }


def test_summarize_agent_test_status_no_runs() -> None:
    with patch("psutil.process_iter") as mock_iter:
        mock_iter.return_value = [
            FakeProcess(101, "python", ["python", "main.py"]),
            FakeProcess(102, "chrome", ["chrome", "https://google.com"]),
        ]
        status = summarize_agent_test_status()
        assert status == {
            "synthetic": {"running": False, "pids": []},
            "prompting": {"running": False, "pids": []},
            "live_turn": {"running": False, "pids": []},
        }


def test_summarize_agent_test_status_running_suites() -> None:
    with patch("psutil.process_iter") as mock_iter:
        mock_iter.return_value = [
            FakeProcess(201, "pytest", ["pytest", "-m", "synthetic", "tests/synthetic/"]),
            FakeProcess(
                202,
                "pytest",
                ["pytest", "-m", "not live_llm", "tests/core/agent/test_turn_scenarios.py"],
            ),
            FakeProcess(
                203, "python", ["python", "-m", "infra.ci.run_live_turn_shards", "--shards", "4"]
            ),
        ]
        status = summarize_agent_test_status()
        assert status["synthetic"]["running"] is True
        assert 201 in status["synthetic"]["pids"]

        assert status["prompting"]["running"] is True
        assert 202 in status["prompting"]["pids"]

        assert status["live_turn"]["running"] is True
        assert 203 in status["live_turn"]["pids"]


def test_create_agent_regression_scenario_json_list() -> None:
    transcript_data = [
        {"user": "U123", "text": "Can you check if Sentry is connected?"},
        {
            "bot_id": "B456",
            "text": "Let me run a check. Running /integrations list and Executing opensre investigate --service sentry",
        },
        {"bot_id": "B456", "text": "The integrations are connected. Sentry is working properly."},
    ]
    transcript_str = json.dumps(transcript_data)

    res = create_agent_regression_scenario(
        transcript=transcript_str,
        id="206-sentry-test",
        title="Sentry Verification Scenario",
        behavior_class="local_execution",
    )

    assert res["success"] is True
    yaml_str = res["yaml"]
    assert yaml_str is not None

    data = yaml.safe_load(yaml_str)
    assert data["id"] == "206-sentry-test"
    assert data["title"] == "Sentry Verification Scenario"
    assert data["intent_class"] == "local_execution"
    assert data["input"]["prompt"] == "Can you check if Sentry is connected?"
    assert "sentry" in data["session"]["configured_integrations"]

    # Verify actions extracted
    planned = data["planned_actions"]
    assert len(planned) == 2
    assert planned[0]["kind"] == "slash"
    assert planned[0]["command"] == "/integrations"
    assert planned[0]["args"] == ["list"]

    assert planned[1]["kind"] == "cli_command"
    assert planned[1]["payload"] == "investigate --service sentry"

    tool_acts = data["tool_actions"]
    assert len(tool_acts) == 2
    assert tool_acts[0]["surface"] == "dispatch"
    assert tool_acts[0]["kind"] == "slash"
    assert tool_acts[1]["kind"] == "cli_command"

    # Verify response contract contains the clean bot reply
    assert (
        "The integrations are connected. Sentry is working properly."
        in data["response_contract"]["must_contain_any"]
    )


def test_create_agent_regression_scenario_plain_text() -> None:
    transcript_str = """show me connected integrations
Running /integrations list
Success!
"""
    res = create_agent_regression_scenario(
        transcript=transcript_str,
        id="207-plain-text",
        title="Plain Text Scenario",
        behavior_class="local_execution",
    )
    assert res["success"] is True
    data = yaml.safe_load(res["yaml"])
    assert data["id"] == "207-plain-text"
    assert data["input"]["prompt"] == "show me connected integrations"
    assert len(data["planned_actions"]) == 1
    assert data["planned_actions"][0]["kind"] == "slash"
    assert data["planned_actions"][0]["command"] == "/integrations"


def test_create_agent_regression_scenario_write_to_file(tmp_path: Path) -> None:
    transcript_str = "check health\nRunning opensre health\nAll ok"
    out_file = tmp_path / "scenario.yml"

    res = create_agent_regression_scenario(
        transcript=transcript_str,
        id="208-write-file",
        title="Write File Scenario",
        behavior_class="local_execution",
        output_path=str(out_file),
    )

    assert res["success"] is True
    assert out_file.is_file()

    content = out_file.read_text(encoding="utf-8")
    data = yaml.safe_load(content)
    assert data["id"] == "208-write-file"
    assert data["input"]["prompt"] == "check health"
