"""Slack failure transcript parser to extract turn scenario inputs, actions, and contracts."""

from __future__ import annotations

import re
from typing import Any


def clean_line(line: str) -> str:
    """Remove leading Slack timestamps like [10:00 AM] or 12:34:56 and whitespace."""
    line = line.strip()
    cleaned = re.sub(
        r"^\[?\d{1,2}:\d{2}(?::\d{2})?\s*(?:[AP]M)?\]?\s*",
        "",
        line,
        flags=re.IGNORECASE,
    )
    return cleaned.strip()


def parse_transcript(transcript: str) -> tuple[str, list[dict[str, Any]], list[str]]:
    """Parse a raw Slack transcript into prompt, actions, and response content."""
    lines = [clean_line(line) for line in transcript.splitlines()]
    lines = [line for line in lines if line]

    prompt: str | None = None
    actions: list[dict[str, Any]] = []
    response_lines: list[str] = []

    for line in lines:
        line_lower = line.lower()

        # 1. Identify User/Prompt prefixes
        user_match = re.match(
            r"^(?:user|prompt|umesh-bhati|umesh)\s*:\s*(.*)",
            line,
            re.IGNORECASE,
        )
        if user_match:
            val = user_match.group(1).strip()
            if prompt is None:
                prompt = val
            else:
                response_lines.append(val)
            continue

        # Check for Agent/Response prefixes
        agent_match = re.match(
            r"^(?:agent\s+response|agent|response|output|opensre|bot|tracer-bot)\s*:\s*(.*)",
            line,
            re.IGNORECASE,
        )
        if agent_match:
            val = agent_match.group(1).strip()
            # If the output line looks like an action command, parse it as a command
            if (
                val.startswith("/")
                or val.lower().startswith("opensre ")
                or "synthetic" in val.lower()
                or re.search(r"\w+:\d{3}-\S+", val)
            ):
                line = val
                line_lower = val.lower()
            else:
                response_lines.append(val)
                continue

        # 2. Identify Action Kinds
        # Slash Command
        if line.startswith("/"):
            parts = line.split()
            cmd = parts[0]
            if len(cmd) > 1 and cmd[1].isalpha():
                actions.append(
                    {
                        "kind": "slash",
                        "command": cmd,
                        "args": parts[1:],
                        "content": line,
                    }
                )
                continue

        # Synthetic Test (e.g. rds_postgres:003-storage-full)
        synth_match = re.search(r"(\w+):(\d{3}-\S+)", line)
        if synth_match:
            suite, scenario = synth_match.groups()
            actions.append(
                {
                    "kind": "synthetic_test",
                    "suite": suite,
                    "scenario": scenario,
                    "content": f"{suite}:{scenario}",
                }
            )
            continue

        # CLI Command (e.g. opensre integrations verify --dry-run)
        if (
            line_lower.startswith("opensre ")
            or "integrations verify" in line_lower
            or line_lower.startswith("opensre investigate")
        ):
            cmd_text = line
            if cmd_text.lower().startswith("opensre "):
                cmd_text = cmd_text[8:].strip()
            actions.append(
                {
                    "kind": "cli_command",
                    "payload": cmd_text,
                    "content": cmd_text,
                }
            )
            continue

        # 3. Fallback lines
        if prompt is None:
            prompt = line
        else:
            response_lines.append(line)

    if prompt is None:
        prompt = "test prompt"

    return prompt, actions, response_lines
