#!/usr/bin/env python3
"""One-shot migration: executed_actions + gathered_tools_contract -> tool_actions."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

SCENARIOS_DIR = Path(__file__).resolve().parents[1] / "app/cli/interactive_shell/routing/tests/scenarios"


def _legacy_to_tool_actions(data: dict[str, Any]) -> list[dict[str, Any]]:
    tool_actions: list[dict[str, Any]] = []

    for action in data.get("executed_actions") or []:
        if not isinstance(action, dict):
            continue
        entry = {"surface": "dispatch", **action}
        tool_actions.append(entry)

    contract = data.get("gathered_tools_contract")
    if not isinstance(contract, dict):
        return tool_actions

    expect_fields = (
        ("must_not_call", "not_called"),
        ("must_call_all", "called"),
        ("must_call_any", "call_any"),
        ("must_return_valid_data", "valid_data"),
        ("must_return_valid_data_any", "valid_data_any"),
    )
    for field, expect in expect_fields:
        tools = contract.get(field)
        if not tools:
            continue
        if not isinstance(tools, list):
            continue
        names = [str(item).strip() for item in tools if str(item).strip()]
        if not names:
            continue
        if len(names) == 1:
            tool_actions.append({"surface": "gather", "tool": names[0], "expect": expect})
        else:
            tool_actions.append({"surface": "gather", "tools": names, "expect": expect})

    return tool_actions


def _quote_hash_in_notes(text: str) -> str:
    """Quote YAML note bullets that contain ``#`` so safe_load does not truncate them."""
    lines = text.splitlines()
    out: list[str] = []
    in_notes = False
    for line in lines:
        if line.startswith("notes:"):
            in_notes = True
            out.append(line)
            continue
        if in_notes:
            if line.startswith("- ") and "#" in line[2:] and not (
                line[2:].startswith("'") or line[2:].startswith('"')
            ):
                out.append(f"- '{line[2:]}'")
                continue
            if line and not line.startswith("- ") and not line.startswith(" "):
                in_notes = False
        out.append(line)
    return "\n".join(out) + ("\n" if text.endswith("\n") else "")


def migrate_file(path: Path) -> bool:
    text = path.read_text(encoding="utf-8")
    if "tool_actions:" in text:
        return False
    if "executed_actions" not in text and "gathered_tools_contract" not in text:
        raw = yaml.safe_load(_quote_hash_in_notes(text))
        if not isinstance(raw, dict):
            return False
        raw["tool_actions"] = []
        raw.pop("executed_actions", None)
        raw.pop("gathered_tools_contract", None)
        path.write_text(
            yaml.safe_dump(raw, sort_keys=False, allow_unicode=True, width=100),
            encoding="utf-8",
        )
        return True

    raw = yaml.safe_load(_quote_hash_in_notes(text))
    if not isinstance(raw, dict):
        return False
    raw["tool_actions"] = _legacy_to_tool_actions(raw)
    raw.pop("executed_actions", None)
    raw.pop("gathered_tools_contract", None)
    path.write_text(
        yaml.safe_dump(raw, sort_keys=False, allow_unicode=True, width=100),
        encoding="utf-8",
    )
    return True


def main() -> None:
    migrated = 0
    for path in sorted(SCENARIOS_DIR.rglob("*.yml")):
        if migrate_file(path):
            migrated += 1
            print(f"migrated {path.relative_to(SCENARIOS_DIR.parents[2])}")
    print(f"done: {migrated} files")


if __name__ == "__main__":
    main()
