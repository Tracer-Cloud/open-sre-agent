"""Agent regression tracking and turn-scenario creation tool package."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from core.tool_framework.tool_decorator import tool
from tools.system.agent_regression_tool.monitor import get_running_test_suites
from tools.system.agent_regression_tool.parser import parse_transcript

_INTENT_TO_BEHAVIOR_CLASS: dict[str, str] = {
    "chat_handoff": "chat_handoff",
    "local_execution": "local_execution",
    "investigation": "investigations",
    "complex_shell_prompts": "complex_shell_prompts",
    "compound": "compound",
    "remote": "remote",
    "follow_up": "follow_up",
    "non_actionable": "non_actionable",
}


@tool(
    name="summarize_agent_test_status",
    display_name="Summarize agent test status",
    source="interactive_shell",
    description="Check the system for running agent test suites: synthetic, prompting, and live-turn tests.",
    use_cases=[
        "Determining if agent tests are currently running",
        "Finding active process IDs and command lines for running test suites",
    ],
    tags=("safe", "fast", "no-credentials"),
    input_schema={
        "type": "object",
        "properties": {},
        "required": [],
    },
)
def summarize_agent_test_status() -> dict[str, Any]:
    """Check the system for running agent test suites: synthetic, prompting, and live-turn tests."""
    suites = get_running_test_suites()

    summary_parts = []
    for suite_name, processes in suites.items():
        if processes:
            pids = ", ".join(str(p["pid"]) for p in processes)
            summary_parts.append(f"{suite_name} (RUNNING, PIDs: {pids})")
        else:
            summary_parts.append(f"{suite_name} (inactive)")

    return {
        "running_suites": suites,
        "summary": "Agent test suites status: " + ", ".join(summary_parts),
    }


@tool(
    name="create_agent_regression_scenario",
    display_name="Create agent regression scenario",
    source="interactive_shell",
    description="Convert a Slack failure transcript into a replayable turn scenario YAML file.",
    use_cases=[
        "Generating regression turn scenarios from copy-pasted Slack error transcripts",
        "Adding new turn scenarios to the test suite to prevent regressions",
    ],
    tags=("safe", "fast", "no-credentials"),
    input_schema={
        "type": "object",
        "properties": {
            "transcript": {
                "type": "string",
                "description": "Slack failure transcript containing user prompt, agent actions, and output.",
            },
            "scenario_id": {
                "type": "string",
                "description": "Unique identifier for the scenario (e.g. 206-sentry-alert-reproduced).",
            },
            "title": {
                "type": "string",
                "description": "Descriptive title for the regression scenario.",
            },
            "intent_class": {
                "type": "string",
                "description": "Intent classification (e.g. local_execution, investigation, chat_handoff). Default: local_execution.",
                "enum": [
                    "chat_handoff",
                    "local_execution",
                    "investigation",
                    "complex_shell_prompts",
                    "compound",
                    "remote",
                    "follow_up",
                    "non_actionable",
                ],
            },
            "configured_integrations": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Integrations configured for this scenario. Default: ['datadog', 'sentry'].",
            },
            "output_path": {
                "type": "string",
                "description": "Custom output path for the YAML file. If omitted, saves to tests/core/agent/scenarios/<behavior_class>/<scenario_id>.yml.",
            },
        },
        "required": ["transcript", "scenario_id", "title"],
    },
)
def create_agent_regression_scenario(
    transcript: str,
    scenario_id: str,
    title: str,
    intent_class: str = "local_execution",
    configured_integrations: list[str] | None = None,
    output_path: str | None = None,
) -> dict[str, Any]:
    """Convert a Slack failure transcript into a replayable turn scenario YAML file."""
    prompt, parsed_actions, response_lines = parse_transcript(transcript)

    planned_actions: list[dict[str, Any]] = []
    tool_actions: list[dict[str, Any]] = []
    history_expected: list[dict[str, Any]] = []

    slash_commands: set[str] = set()
    cli_commands: set[str] = set()
    synthetic_suites: set[str] = set()

    executes_terminal_action = len(parsed_actions) > 0

    for act in parsed_actions:
        kind = act["kind"]
        if kind == "slash":
            slash_commands.add(act["command"])
            planned_actions.append(
                {
                    "kind": "slash",
                    "content": act["content"],
                    "source": "llm",
                    "target_surface": "slash",
                    "command": act["command"],
                    "args": act["args"],
                }
            )
            tool_actions.append(
                {
                    "surface": "dispatch",
                    "kind": "slash",
                    "command": act["command"],
                    "args": act["args"],
                    "content": act["content"],
                }
            )
            history_expected.append({"type": "slash", "ok": True})
        elif kind == "cli_command":
            # available capability is the base verb (e.g. integrations)
            verb = act["payload"].split()[0]
            cli_commands.add(verb)
            planned_actions.append(
                {
                    "kind": "cli_command",
                    "content": act["content"],
                    "source": "llm",
                    "target_surface": "terminal",
                    "payload": act["payload"],
                }
            )
            tool_actions.append(
                {
                    "surface": "dispatch",
                    "kind": "cli_command",
                    "payload": act["payload"],
                    "content": act["content"],
                }
            )
            history_expected.append({"type": "cli_command", "ok": True})
        elif kind == "synthetic_test":
            synthetic_suites.add(act["suite"])
            planned_actions.append(
                {
                    "kind": "synthetic_test",
                    "source": "llm",
                    "target_surface": "investigation",
                    "suite": act["suite"],
                    "scenario": act["scenario"],
                    "content": act["content"],
                }
            )
            tool_actions.append(
                {
                    "surface": "dispatch",
                    "kind": "synthetic_test",
                    "suite": act["suite"],
                    "scenario": act["scenario"],
                    "content": act["content"],
                }
            )
            history_expected.append({"type": "synthetic_test", "ok": True})

    # Default to assistant_handoff if no actions were mapped
    if not executes_terminal_action:
        planned_actions.append({"kind": "assistant_handoff"})

    if configured_integrations is None:
        configured_integrations = ["datadog", "sentry"]

    must_contain_any: list[str] = []
    for line in response_lines:
        clean = line.strip()
        # Only check against non-trivial phrases/lines
        if clean and not clean.startswith("/") and len(clean) > 3:
            must_contain_any.append(clean)

    if not must_contain_any:
        must_contain_any = ["Ran action" if executes_terminal_action else "Answered"]

    # Build target output path
    if output_path is None:
        # Repository root is three levels up from tools/system/agent_regression_tool/
        repo_root = Path(__file__).resolve().parents[3]
        behavior_class = _INTENT_TO_BEHAVIOR_CLASS.get(intent_class, "local_execution")
        target_file = (
            repo_root
            / "tests"
            / "core"
            / "agent"
            / "scenarios"
            / behavior_class
            / f"{scenario_id}.yml"
        )
    else:
        target_file = Path(output_path)

    # Ensure parent directory exists
    target_file.parent.mkdir(parents=True, exist_ok=True)

    # Construct the YAML dictionary schema
    scenario_data = {
        "id": scenario_id,
        "title": title,
        "intent_class": intent_class,
        "input": {"prompt": prompt},
        "session": {
            "has_prior_state": False,
            "configured_integrations": sorted(configured_integrations),
            "resolved_integrations": {},
        },
        "available_capabilities": {
            "slash_commands": sorted(slash_commands) if slash_commands else [],
            "cli_commands": sorted(cli_commands) if cli_commands else [],
            "synthetic_suites": (sorted(synthetic_suites) if synthetic_suites else []),
        },
        "notes": [],
        "turn": {"expected_kind": "agent"},
        "policy": {"executes_terminal_action": executes_terminal_action},
        "planned_actions": planned_actions,
        "response_contract": {
            "must_contain_any": must_contain_any,
            "must_not_contain": [],
        },
        "history": {"expected": history_expected},
        "runs": 1,
        "tool_actions": tool_actions,
    }

    # Write block-style YAML representation
    with open(target_file, "w", encoding="utf-8") as f:
        yaml.safe_dump(scenario_data, f, default_flow_style=False, sort_keys=False)

    return {
        "scenario_id": scenario_id,
        "file_path": str(target_file),
        "prompt": prompt,
        "executes_terminal_action": executes_terminal_action,
        "planned_actions": planned_actions,
        "must_contain_any": must_contain_any,
    }


__all__ = [
    "create_agent_regression_scenario",
    "summarize_agent_test_status",
]
