"""Agent regression tool — monitor suites and generate regression turn scenarios.

Provides capabilities to summarize current agent test runs and convert Slack failure
transcripts into valid scenario YAML configs.
"""

from __future__ import annotations

import contextlib
import json
import logging
import re
from pathlib import Path
from typing import Any

import psutil
import yaml

from tools.tool_decorator import tool

logger = logging.getLogger(__name__)

# Reverse mapping for intent class resolution
BEHAVIOR_TO_INTENT_CLASS = {
    "chat_handoff": "chat_handoff",
    "local_execution": "local_execution",
    "investigations": "investigation",
    "complex_shell_prompts": "complex_shell_prompts",
    "compound": "compound",
    "remote": "remote",
    "follow_up": "follow_up",
    "non_actionable": "non_actionable",
}


@tool(
    name="summarize_agent_test_status",
    source="knowledge",
    description=(
        "Query currently running processes to report whether synthetic, "
        "prompting (deterministic turn checks), or live-turn agent test suites "
        "are actively executing."
    ),
    surfaces=("investigation", "chat"),
    input_schema={
        "type": "object",
        "properties": {},
        "required": [],
    },
)
def summarize_agent_test_status(**_kwargs: Any) -> dict[str, Any]:
    """Report running test suites by checking process info."""
    status: dict[str, dict[str, Any]] = {
        "synthetic": {"running": False, "pids": []},
        "prompting": {"running": False, "pids": []},
        "live_turn": {"running": False, "pids": []},
    }

    for proc in psutil.process_iter(["pid", "name", "cmdline"]):
        try:
            cmdline = proc.info.get("cmdline")
            if not cmdline:
                continue

            cmd_str = " ".join(cmdline).lower()

            if not any(x in cmd_str for x in ["python", "pytest", "make", "uv"]):
                continue

            # Detect Live-Turn
            is_live_turn = False
            if (
                "run_live_turn_shards" in cmd_str
                or "test-turn-live" in cmd_str
                or ("test_turn_scenarios.py" in cmd_str and "not live_llm" not in cmd_str)
            ):
                is_live_turn = True

            # Detect Prompting
            is_prompting = False
            if not is_live_turn and (
                ("not live_llm" in cmd_str and "test_turn_scenarios" in cmd_str)
                or "test_turn_fixture_integrity" in cmd_str
                or "test_prompt" in cmd_str
                or "test-turn-checks" in cmd_str
            ):
                is_prompting = True

            # Detect Synthetic
            is_synthetic = False
            if "synthetic" in cmd_str or "run_suite" in cmd_str:
                is_synthetic = True

            pid = proc.info["pid"]
            if is_live_turn:
                status["live_turn"]["running"] = True
                status["live_turn"]["pids"].append(pid)
            elif is_prompting:
                status["prompting"]["running"] = True
                status["prompting"]["pids"].append(pid)
            elif is_synthetic:
                status["synthetic"]["running"] = True
                status["synthetic"]["pids"].append(pid)

        except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
            continue

    return status


def _parse_transcript(transcript: str) -> tuple[str, list[dict[str, Any]]]:
    """Parse Slack transcript JSON or text, returning prompt and bot message contexts."""
    transcript = transcript.strip()
    if not transcript:
        raise ValueError("Empty transcript.")

    data: Any = None
    with contextlib.suppress(json.JSONDecodeError):
        data = json.loads(transcript)

    messages = []
    if isinstance(data, list):
        messages = data
    elif isinstance(data, dict):
        for key in ("messages", "history", "transcript", "turns"):
            if isinstance(data.get(key), list):
                messages = data[key]
                break
        if not messages and ("text" in data or "content" in data):
            messages = [data]

    if not messages:
        lines = [line.strip() for line in transcript.splitlines() if line.strip()]
        if not lines:
            raise ValueError("Empty transcript.")
        prompt = lines[0]
        bot_msgs = [{"text": line} for line in lines[1:]]
        return prompt, bot_msgs

    prompt = ""
    bot_msgs = []

    for msg in messages:
        text = msg.get("text") or msg.get("content") or ""
        if not text:
            continue

        is_bot = (
            bool(msg.get("bot_id"))
            or msg.get("subtype") == "bot_message"
            or str(msg.get("user", "")).lower() in ("bot", "agent", "opensre", "assistant")
            or ("username" in msg and str(msg["username"]).lower() == "opensre")
        )

        if is_bot:
            bot_msgs.append({"text": text})
        else:
            if not prompt:
                prompt = text

    if not prompt and messages:
        first_msg = messages[0]
        prompt = first_msg.get("text") or first_msg.get("content") or ""
        bot_msgs = [{"text": m.get("text") or m.get("content") or ""} for m in messages[1:]]

    return prompt.strip(), bot_msgs


def _extract_slash_command(line: str) -> tuple[str, list[str]] | None:
    """Robustly extract slash command and arguments, stopping at transition/stop words."""
    match = re.search(r"(/[a-zA-Z0-9_-]+)", line)
    if not match:
        return None
    cmd = match.group(1)
    text_after = line[match.end() :]

    args = []
    stop_words = {"and", "then", "to", "with", "for", "executing", "ran", "running", "but", "so"}
    words = text_after.split()
    for w in words:
        w_clean = w.strip(".,;:?!`'\"")
        if w_clean.lower() in stop_words:
            break
        if w.startswith("/") or w_clean == "opensre":
            break
        args.append(w_clean)

    return cmd, args


def _extract_cli_command(line: str) -> str | None:
    """Robustly extract opensre CLI command payload, stopping at transition/stop words."""
    idx = line.find("opensre ")
    if idx == -1:
        return None
    text_after = line[idx + len("opensre ") :].strip()

    words = text_after.split()
    args = []
    stop_words = {"and", "then", "to", "with", "for", "executing", "ran", "running", "but", "so"}
    for w in words:
        w_clean = w.strip(".,;:?!`'\"")
        if w_clean.lower() in stop_words:
            break
        if w.startswith("/"):
            break
        args.append(w_clean)

    if not args:
        return None
    return " ".join(args)


@tool(
    name="create_agent_regression_scenario",
    source="knowledge",
    description=(
        "Generate a replayable scenario YAML definition from a Slack "
        "failure transcript to serve as an agent regression test case."
    ),
    surfaces=("investigation", "chat"),
    input_schema={
        "type": "object",
        "properties": {
            "transcript": {
                "type": "string",
                "description": "Slack transcript dump (JSON list/dict or plain text lines)",
            },
            "id": {
                "type": "string",
                "description": "Unique scenario ID (e.g. 206-sentry-retry-fail)",
            },
            "title": {
                "type": "string",
                "description": "Descriptive title of the scenario case",
            },
            "behavior_class": {
                "type": "string",
                "description": "Target folder / behavior class for test execution",
                "default": "local_execution",
            },
            "output_path": {
                "type": "string",
                "description": "Optional absolute path to write the generated YAML to",
                "default": "",
            },
        },
        "required": ["transcript", "id", "title"],
    },
)
def create_agent_regression_scenario(
    transcript: str,
    scenario_id: str,
    title: str,
    behavior_class: str = "local_execution",
    output_path: str = "",
    **_kwargs: Any,
) -> dict[str, Any]:
    """Parse transcript and output structured turn scenario YAML."""
    try:
        prompt, bot_msgs = _parse_transcript(transcript)
    except Exception as err:
        return {"success": False, "error": f"Failed to parse transcript: {err}"}

    # Extract planned/executed actions
    planned_actions = []
    tool_actions = []
    history_expected = []
    executes_terminal_action = False

    # Track unique command signatures to prevent duplicates
    seen_commands = set()

    for msg in bot_msgs:
        text = msg.get("text", "")
        for line in text.splitlines():
            line = line.strip()

            # Look for slash commands
            slash_info = _extract_slash_command(line)
            if slash_info:
                cmd, args = slash_info
                full_cmd = f"{cmd} {' '.join(args)}".strip()

                if full_cmd not in seen_commands:
                    seen_commands.add(full_cmd)
                    executes_terminal_action = True
                    planned_actions.append(
                        {
                            "kind": "slash",
                            "content": full_cmd,
                            "source": "llm",
                            "target_surface": "slash",
                            "command": cmd,
                            "args": args,
                        }
                    )
                    tool_actions.append(
                        {
                            "surface": "dispatch",
                            "kind": "slash",
                            "command": cmd,
                            "args": args,
                            "content": full_cmd,
                        }
                    )
                    history_expected.extend(
                        [
                            {"type": "cli_agent", "ok": True},
                            {"type": "slash", "ok": True},
                        ]
                    )

            # Look for CLI command pattern (stripped of opensre prefix)
            payload = _extract_cli_command(line)
            if payload and payload not in seen_commands:
                seen_commands.add(payload)
                executes_terminal_action = True
                planned_actions.append(
                    {
                        "kind": "cli_command",
                        "content": payload,
                        "source": "llm",
                        "target_surface": "terminal",
                        "payload": payload,
                    }
                )
                tool_actions.append(
                    {
                        "surface": "dispatch",
                        "kind": "cli_command",
                        "payload": payload,
                        "content": payload,
                    }
                )
                history_expected.append({"type": "cli_command", "ok": True})

    # Extract simple reply assertions for must_contain_any
    must_contain_any = []
    for msg in bot_msgs:
        text = msg.get("text", "").strip()
        if not text:
            continue
        for line in text.splitlines():
            line = line.strip()
            if not line or "opensre " in line or line.startswith("/"):
                continue
            # Strip markdown characters
            line_clean = re.sub(r"[*`_#\-]", "", line).strip()
            if 5 < len(line_clean) < 100:
                must_contain_any.append(line_clean)
                break

    # Reconcile integrations configured
    configured_integrations = []
    for integration in ["datadog", "sentry", "aws", "grafana", "slack", "github"]:
        if integration in prompt.lower():
            configured_integrations.append(integration)
    if not configured_integrations:
        configured_integrations = ["datadog", "sentry"]

    # Capabilities lists
    slash_commands = []
    cli_commands = []
    for act in planned_actions:
        if act["kind"] == "slash":
            slash_commands.append(act["command"])
        elif act["kind"] == "cli_command":
            # Extract main command keyword from payload
            payload_val = act.get("payload")
            if isinstance(payload_val, str):
                parts = payload_val.split()
                if parts:
                    cli_commands.append(parts[0])

    intent_class = BEHAVIOR_TO_INTENT_CLASS.get(behavior_class, behavior_class)

    scenario_data = {
        "id": id,
        "title": title,
        "intent_class": intent_class,
        "input": {
            "prompt": prompt,
        },
        "session": {
            "has_prior_state": False,
            "configured_integrations": configured_integrations,
            "resolved_integrations": {},
        },
        "available_capabilities": {
            "slash_commands": slash_commands,
            "cli_commands": cli_commands,
            "synthetic_suites": [],
        },
        "notes": [],
        "turn": {
            "expected_kind": "agent",
        },
        "policy": {
            "executes_terminal_action": executes_terminal_action,
        },
        "planned_actions": planned_actions,
        "response_contract": {
            "must_contain_any": must_contain_any,
            "must_not_contain": [],
        },
        "history": {
            "expected": history_expected,
        },
        "runs": 1,
        "tool_actions": tool_actions,
    }

    yaml_content = yaml.dump(
        scenario_data, default_flow_style=False, sort_keys=False, allow_unicode=True
    )

    if output_path:
        try:
            path = Path(output_path).resolve()
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(yaml_content, encoding="utf-8")
        except Exception as err:
            return {
                "success": False,
                "error": f"Failed to write output file: {err}",
                "yaml": yaml_content,
            }

    return {"success": True, "yaml": yaml_content, "output_path": output_path or None}
