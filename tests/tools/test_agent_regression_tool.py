"""Tests for agent_regression_tool."""

from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import yaml

from tests.core.agent.scenario_loader import validate_action_shape
from tests.tools.conftest import BaseToolContract
from tools.system.agent_regression_tool import (
    create_agent_regression_scenario,
    summarize_agent_test_status,
)


class TestSummarizeStatusContract(BaseToolContract):
    def get_tool_under_test(self) -> Any:
        return summarize_agent_test_status.__opensre_registered_tool__  # type: ignore[attr-defined]


class TestCreateScenarioContract(BaseToolContract):
    def get_tool_under_test(self) -> Any:
        return create_agent_regression_scenario.__opensre_registered_tool__  # type: ignore[attr-defined]


def test_summarize_agent_test_status_no_running_processes() -> None:
    # Mock monitor.process_iter to return no processes
    with patch("tools.system.agent_regression_tool.monitor.process_iter") as mock_iter:
        mock_iter.return_value = []
        result = summarize_agent_test_status()

        assert "Agent test suites status" in result["summary"]
        assert "synthetic (inactive)" in result["summary"]
        assert "prompting (inactive)" in result["summary"]
        assert "live-turn (inactive)" in result["summary"]
        assert result["running_suites"] == {
            "synthetic": [],
            "prompting": [],
            "live-turn": [],
        }


def test_summarize_agent_test_status_with_running_processes() -> None:
    # Mock psutil.process_iter to return fake processes running tests
    mock_proc1 = MagicMock()
    mock_proc1.info = {
        "pid": 1234,
        "name": "python",
        "cmdline": ["python", "-m", "pytest", "-m", "synthetic", "tests/synthetic/"],
    }

    mock_proc2 = MagicMock()
    mock_proc2.info = {
        "pid": 5678,
        "name": "pytest",
        "cmdline": [
            "pytest",
            "tests/core/agent/prompts/test_prompt_characterization.py",
        ],
    }

    mock_proc3 = MagicMock()
    mock_proc3.info = {
        "pid": 9012,
        "name": "python",
        "cmdline": [
            "python",
            ".github/ci/run_live_turn_shards.py",
            "--shards",
            "4",
        ],
    }

    with patch("tools.system.agent_regression_tool.monitor.process_iter") as mock_iter:
        mock_iter.return_value = [mock_proc1, mock_proc2, mock_proc3]
        result = summarize_agent_test_status()

        assert "synthetic (RUNNING, PIDs: 1234)" in result["summary"]
        assert "prompting (RUNNING, PIDs: 5678)" in result["summary"]
        assert "live-turn (RUNNING, PIDs: 9012)" in result["summary"]
        assert len(result["running_suites"]["synthetic"]) == 1
        assert result["running_suites"]["synthetic"][0]["pid"] == 1234


def test_create_agent_regression_scenario_slash_command(
    tmp_path: Path,
) -> None:
    transcript = """
    User: check my watches status
    OpenSRE: /watch status
    Agent Response: Here is the status of active watches.
    """
    output_file = tmp_path / "test_scenario.yml"

    result = create_agent_regression_scenario(
        transcript=transcript,
        scenario_id="999-watch-status-check",
        title="Check watches status regression",
        intent_class="local_execution",
        output_path=str(output_file),
    )

    assert result["scenario_id"] == "999-watch-status-check"
    assert result["prompt"] == "check my watches status"
    assert result["executes_terminal_action"] is True
    assert len(result["planned_actions"]) == 1
    assert result["planned_actions"][0]["kind"] == "slash"
    assert result["planned_actions"][0]["command"] == "/watch"
    assert result["planned_actions"][0]["args"] == ["status"]

    # Verify file actually written
    assert output_file.is_file()
    with open(output_file, encoding="utf-8") as f:
        data = yaml.safe_load(f)

    assert data["id"] == "999-watch-status-check"
    assert data["input"]["prompt"] == "check my watches status"
    assert data["available_capabilities"]["slash_commands"] == ["/watch"]
    assert data["policy"]["executes_terminal_action"] is True
    assert data["response_contract"]["must_contain_any"] == [
        "Here is the status of active watches."
    ]

    # Validate action shape using harness validation to make sure it is completely correct
    for index, action in enumerate(data["planned_actions"]):
        validate_action_shape(action, prefix=f"Planned action {index}", require_source=True)
    for index, action in enumerate(data["tool_actions"]):
        validate_action_shape(action, prefix=f"Executed action {index}", require_source=False)


def test_create_agent_regression_scenario_cli_command(
    tmp_path: Path,
) -> None:
    transcript = """
    [09:00:00] Umesh-Bhati: runs a verification check
    [09:00:05] OpenSRE: opensre integrations verify --dry-run
    [09:00:10] Bot: Verification complete.
    """
    output_file = tmp_path / "test_cli_scenario.yml"

    result = create_agent_regression_scenario(
        transcript=transcript,
        scenario_id="999-cli-verify",
        title="CLI integrations verify check",
        intent_class="local_execution",
        output_path=str(output_file),
    )

    assert result["prompt"] == "runs a verification check"
    assert result["executes_terminal_action"] is True
    assert result["planned_actions"][0]["kind"] == "cli_command"
    assert result["planned_actions"][0]["payload"] == "integrations verify --dry-run"
    assert result["must_contain_any"] == ["Verification complete."]

    with open(output_file, encoding="utf-8") as f:
        data = yaml.safe_load(f)

    assert data["available_capabilities"]["cli_commands"] == ["integrations"]
    for index, action in enumerate(data["planned_actions"]):
        validate_action_shape(action, prefix=f"Planned action {index}", require_source=True)


def test_create_agent_regression_scenario_synthetic_test(
    tmp_path: Path,
) -> None:
    transcript = """
    User: please run the storage full synthetic scenario
    Agent: rds_postgres:003-storage-full
    Response: Synthetic test run completed.
    """
    output_file = tmp_path / "test_synth_scenario.yml"

    result = create_agent_regression_scenario(
        transcript=transcript,
        scenario_id="999-synth-test",
        title="Synthetic storage full check",
        intent_class="investigation",
        output_path=str(output_file),
    )

    assert result["prompt"] == "please run the storage full synthetic scenario"
    assert result["executes_terminal_action"] is True
    assert result["planned_actions"][0]["kind"] == "synthetic_test"
    assert result["planned_actions"][0]["suite"] == "rds_postgres"
    assert result["planned_actions"][0]["scenario"] == "003-storage-full"

    with open(output_file, encoding="utf-8") as f:
        data = yaml.safe_load(f)

    assert data["available_capabilities"]["synthetic_suites"] == ["rds_postgres"]
    for index, action in enumerate(data["planned_actions"]):
        validate_action_shape(action, prefix=f"Planned action {index}", require_source=True)


def test_create_agent_regression_scenario_handoff(
    tmp_path: Path,
) -> None:
    transcript = """
    User: hello how can you help me?
    Response: I can help you monitor and investigate pipeline failures.
    """
    output_file = tmp_path / "test_handoff.yml"

    result = create_agent_regression_scenario(
        transcript=transcript,
        scenario_id="999-handoff",
        title="Handoff greeting check",
        intent_class="chat_handoff",
        output_path=str(output_file),
    )

    assert result["executes_terminal_action"] is False
    assert result["planned_actions"] == [{"kind": "assistant_handoff"}]
    assert result["must_contain_any"] == [
        "I can help you monitor and investigate pipeline failures."
    ]

    with open(output_file, encoding="utf-8") as f:
        data = yaml.safe_load(f)

    assert data["policy"]["executes_terminal_action"] is False
    assert data["planned_actions"] == [{"kind": "assistant_handoff"}]
