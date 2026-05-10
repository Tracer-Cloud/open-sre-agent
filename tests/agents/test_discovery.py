"""Tests for AI-agent process discovery."""

from __future__ import annotations

from typing import Any

import pytest

from app.agents import discovery


class FakeProcess:
    def __init__(self, info: dict[str, Any]) -> None:
        self.info = info


def test_discover_agent_processes_matches_known_agent_commands(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(discovery.os, "getpid", lambda: 10)
    monkeypatch.setattr(
        discovery.psutil,
        "process_iter",
        lambda **_kwargs: [
            FakeProcess({"pid": 10, "name": "opensre", "cmdline": ["opensre"]}),
            FakeProcess({"pid": 101, "name": "Claude", "cmdline": ["claude", "chat"]}),
            FakeProcess(
                {
                    "pid": 102,
                    "name": "claude",
                    "cmdline": ["claude", "code"],
                }
            ),
            FakeProcess(
                {
                    "pid": 103,
                    "name": "claude",
                    "cmdline": [
                        "/Users/example/.cursor/extensions/anthropic.claude-code/resources/claude",
                        "--output-format",
                        "stream-json",
                        "--input-format",
                        "stream-json",
                    ],
                }
            ),
            FakeProcess({"pid": 104, "name": "aider", "cmdline": ["aider"]}),
            FakeProcess({"pid": 105, "name": "codex", "cmdline": ["codex"]}),
            FakeProcess({"pid": 202, "name": "python", "cmdline": ["python", "-m", "pytest"]}),
        ],
    )

    candidates = discovery.discover_agent_processes()

    assert [(item.name, item.pid) for item in candidates] == [
        ("aider-104", 104),
        ("claude-code-102", 102),
        ("claude-code-103", 103),
        ("codex-105", 105),
    ]


def test_discover_agent_processes_filters_desktop_helper_noise(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(discovery.os, "getpid", lambda: 10)
    monkeypatch.setattr(
        discovery.psutil,
        "process_iter",
        lambda **_kwargs: [
            FakeProcess(
                {
                    "pid": 201,
                    "name": "Claude",
                    "cmdline": ["/Applications/Claude.app/Contents/MacOS/Claude"],
                }
            ),
            FakeProcess(
                {
                    "pid": 202,
                    "name": "chrome_crashpad_handler",
                    "cmdline": [
                        "/Applications/Claude.app/Contents/Frameworks/Electron Framework.framework/Helpers/chrome_crashpad_handler",
                        "--database=/Users/example/Library/Application Support/Claude/Crashpad",
                    ],
                }
            ),
            FakeProcess(
                {
                    "pid": 203,
                    "name": "Claude Helper (Renderer)",
                    "cmdline": [
                        "/Applications/Claude.app/Contents/Frameworks/Claude Helper (Renderer).app/Contents/MacOS/Claude Helper (Renderer)",
                        "--type=renderer",
                    ],
                }
            ),
            FakeProcess(
                {
                    "pid": 204,
                    "name": "Cursor Helper (Plugin)",
                    "cmdline": [
                        "/Applications/Cursor.app/Contents/Frameworks/Cursor Helper (Plugin).app/Contents/MacOS/Cursor Helper (Plugin)",
                        "/Applications/Cursor.app/Contents/Resources/app/extensions/json-language-features/server/dist/node/jsonServerMain",
                    ],
                }
            ),
            FakeProcess(
                {
                    "pid": 205,
                    "name": "ShipIt",
                    "cmdline": [
                        "/Applications/Cursor.app/Contents/Frameworks/Squirrel.framework/Resources/ShipIt",
                        "com.todesktop.230313mzl4w4u92.ShipIt",
                    ],
                }
            ),
        ],
    )

    assert discovery.discover_agent_processes() == []


def test_discover_agent_processes_all_mode_includes_filtered_helpers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(discovery.os, "getpid", lambda: 10)
    monkeypatch.setattr(
        discovery.psutil,
        "process_iter",
        lambda **_kwargs: [
            FakeProcess(
                {
                    "pid": 202,
                    "name": "chrome_crashpad_handler",
                    "cmdline": [
                        "/Applications/Claude.app/Contents/Frameworks/Electron Framework.framework/Helpers/chrome_crashpad_handler",
                    ],
                }
            ),
        ],
    )

    candidates = discovery.discover_agent_processes(include_all=True)

    assert [(item.name, item.pid) for item in candidates] == [("claude-code-202", 202)]


def test_display_command_truncates_long_commands() -> None:
    command = "claude " + ("--very-long-option " * 20)

    display = discovery.display_command(command)

    assert len(display) == 120
    assert display.endswith("...")
