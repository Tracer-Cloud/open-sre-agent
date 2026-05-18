"""Tests for read-only local agent discovery."""

from __future__ import annotations

from pathlib import Path

import pytest

from app.agents import discovery
from app.agents.discovery import ProcessRow, discover_agents, registered_and_discovered_agents
from app.agents.registry import AgentRecord, AgentRegistry


def _patch_codex_rollout_owners(monkeypatch: pytest.MonkeyPatch, owners: set[int]) -> None:
    monkeypatch.setattr(discovery, "process_has_open_codex_rollout", lambda pid: pid in owners)


def test_discover_agent_processes_matches_known_agent_commands(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(discovery.os, "getpid", lambda: 10)
    monkeypatch.setattr(
        discovery,
        "_current_process_rows",
        lambda: [
            ProcessRow(pid=10, command="opensre"),
            ProcessRow(pid=101, command="claude chat"),
            ProcessRow(pid=102, command="claude code"),
            ProcessRow(
                pid=103,
                command=(
                    "/Users/example/.cursor/extensions/anthropic.claude-code/resources/claude "
                    "--output-format stream-json --input-format stream-json"
                ),
            ),
            ProcessRow(pid=104, command="aider"),
            ProcessRow(pid=105, command="codex"),
            ProcessRow(pid=202, command="python -m pytest"),
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
        discovery,
        "_current_process_rows",
        lambda: [
            ProcessRow(pid=201, command="/Applications/Claude.app/Contents/MacOS/Claude"),
            ProcessRow(
                pid=202,
                command=(
                    "/Applications/Claude.app/Contents/Frameworks/Electron "
                    "Framework.framework/Helpers/chrome_crashpad_handler "
                    "--database=/Users/example/Library/Application Support/Claude/Crashpad"
                ),
            ),
            ProcessRow(
                pid=203,
                command=(
                    "/Applications/Claude.app/Contents/Frameworks/Claude Helper "
                    "(Renderer).app/Contents/MacOS/Claude Helper (Renderer) --type=renderer"
                ),
            ),
            ProcessRow(
                pid=204,
                command=(
                    "/Applications/Cursor.app/Contents/Frameworks/Cursor Helper "
                    "(Plugin).app/Contents/MacOS/Cursor Helper (Plugin) "
                    "/Applications/Cursor.app/Contents/Resources/app/extensions/"
                    "json-language-features/server/dist/node/jsonServerMain"
                ),
            ),
            ProcessRow(
                pid=205,
                command=(
                    "/Applications/Cursor.app/Contents/Frameworks/Squirrel.framework/Resources/"
                    "ShipIt com.todesktop.230313mzl4w4u92.ShipIt"
                ),
            ),
        ],
    )

    assert discovery.discover_agent_processes() == []


def test_discover_agent_processes_all_mode_includes_filtered_helpers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(discovery.os, "getpid", lambda: 10)
    monkeypatch.setattr(
        discovery,
        "_current_process_rows",
        lambda: [
            ProcessRow(
                pid=202,
                command=(
                    "/Applications/Claude.app/Contents/Frameworks/Electron "
                    "Framework.framework/Helpers/chrome_crashpad_handler"
                ),
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


def test_parse_ps_line_with_missing_args_keeps_ppid_and_empty_command() -> None:
    row = discovery._parse_ps_line("123 45")

    assert row == ProcessRow(pid=123, ppid=45, command="")


def test_parse_ps_line_with_missing_args_tolerates_invalid_ppid() -> None:
    row = discovery._parse_ps_line("123 not-a-ppid")

    assert row == ProcessRow(pid=123, ppid=None, command="")


def test_discovers_cursor_claude_code_process() -> None:
    records = discover_agents(
        process_rows=[
            ProcessRow(
                pid=80435,
                command=(
                    "/Users/me/.cursor/extensions/anthropic.claude-code-2.1.128-darwin-arm64/"
                    "resources/native-binary/claude --output-format stream-json"
                ),
            )
        ],
        cursor_projects_dir=Path("/does/not/exist"),
    )

    assert len(records) == 1
    assert records[0].name == "cursor-claude-code"
    assert records[0].pid == 80435
    assert records[0].source == "discovered"


def test_discovers_cursor_agent_exec_helper() -> None:
    records = discover_agents(
        process_rows=[
            ProcessRow(
                pid=23995,
                command=(
                    "Cursor Helper (Plugin): extension-host (agent-exec) tracer-agent-2026 [1-4]"
                ),
            )
        ],
        cursor_projects_dir=Path("/does/not/exist"),
    )

    assert [(record.name, record.pid) for record in records] == [("cursor-agent-exec", 23995)]


def test_ignores_generic_desktop_cursor_processes() -> None:
    records = discover_agents(
        process_rows=[
            ProcessRow(pid=23521, command="/Applications/Cursor.app/Contents/MacOS/Cursor"),
            ProcessRow(
                pid=23540,
                command=(
                    "/Applications/Cursor.app/Contents/Frameworks/"
                    "Cursor Helper (Renderer).app/Contents/MacOS/Cursor Helper (Renderer)"
                ),
            ),
        ],
        cursor_projects_dir=Path("/does/not/exist"),
    )

    assert records == []


def test_discovers_agent_cli_from_cursor_terminal_metadata(tmp_path: Path) -> None:
    terminal = tmp_path / "project" / "terminals" / "70.txt"
    terminal.parent.mkdir(parents=True)
    terminal.write_text(
        "---\n"
        "pid: 12345\n"
        "cwd: /repo\n"
        "active_command: claude code\n"
        "last_command: source .venv/bin/activate\n"
        "---\n",
        encoding="utf-8",
    )

    records = discover_agents(process_rows=[], cursor_projects_dir=tmp_path)

    assert [(record.name, record.pid, record.source) for record in records] == [
        ("claude-code", 12345, "discovered")
    ]


def test_ignores_plain_claude_commands_with_code_prefix_arguments() -> None:
    records = discover_agents(
        process_rows=[
            ProcessRow(pid=601, command="claude codebase.py"),
            ProcessRow(pid=602, command="claude codegen --project src"),
        ],
        cursor_projects_dir=Path("/does/not/exist"),
    )

    assert records == []


def test_ignores_non_codex_process_from_codex_named_directory() -> None:
    records = discover_agents(
        process_rows=[
            ProcessRow(
                pid=4242,
                ppid=4200,
                command=(
                    "/workspace/project-with-codex-in-name/.venv/bin/python "
                    "/workspace/project-with-codex-in-name/.venv/bin/opensre"
                ),
            )
        ],
        cursor_projects_dir=Path("/does/not/exist"),
    )

    assert records == []


def test_scan_all_ignores_non_codex_process_from_codex_named_directory(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(discovery.os, "getpid", lambda: 10)
    monkeypatch.setattr(
        discovery,
        "_current_process_rows",
        lambda: [
            ProcessRow(
                pid=4242,
                ppid=4200,
                command=(
                    "/workspace/project-with-codex-in-name/.venv/bin/python "
                    "/workspace/project-with-codex-in-name/.venv/bin/opensre"
                ),
            )
        ],
    )

    assert discovery.discover_agent_processes(include_all=True) == []


def test_discovers_single_codex_row_for_node_wrapper_and_native_child(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_codex_rollout_owners(monkeypatch, {702})

    records = discover_agents(
        process_rows=[
            ProcessRow(pid=701, ppid=1, command="node /Users/me/.local/bin/codex"),
            ProcessRow(
                pid=702,
                ppid=701,
                command="/Users/me/.local/share/codex/vendor/aarch64-apple-darwin/codex/codex",
            ),
        ],
        cursor_projects_dir=Path("/does/not/exist"),
    )

    assert [(record.name, record.pid) for record in records] == [("codex", 702)]


def test_codex_dedupe_runs_after_cursor_terminal_metadata(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    terminal = tmp_path / "project" / "terminals" / "71.txt"
    terminal.parent.mkdir(parents=True)
    terminal.write_text(
        "---\npid: 711\ncwd: /repo\nactive_command: codex\n---\n",
        encoding="utf-8",
    )
    _patch_codex_rollout_owners(monkeypatch, {712})

    records = discover_agents(
        process_rows=[
            ProcessRow(pid=711, ppid=1, command="node /Users/me/.local/bin/codex"),
            ProcessRow(
                pid=712,
                ppid=711,
                command="/Users/me/.local/share/codex/vendor/aarch64-apple-darwin/codex/codex",
            ),
        ],
        cursor_projects_dir=tmp_path,
    )

    assert [(record.name, record.pid) for record in records] == [("codex", 712)]


def test_discover_agent_processes_deduplicates_codex_wrapper_child_in_all_mode(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    rows = [
        ProcessRow(pid=10, command="opensre"),
        ProcessRow(pid=801, ppid=1, command="node /Users/me/.local/bin/codex"),
        ProcessRow(
            pid=802,
            ppid=801,
            command="/Users/me/.local/share/codex/vendor/aarch64-apple-darwin/codex/codex",
        ),
    ]
    _patch_codex_rollout_owners(monkeypatch, {802})
    monkeypatch.setattr(discovery.os, "getpid", lambda: 10)
    monkeypatch.setattr(discovery, "_current_process_rows", lambda: rows)

    candidates = discovery.discover_agent_processes(include_all=True)

    assert [(item.name, item.pid) for item in candidates] == [("codex-802", 802)]


def test_codex_wrapper_native_dedupe_prefers_pid_with_open_rollout(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_codex_rollout_owners(monkeypatch, {901})

    records = discover_agents(
        process_rows=[
            ProcessRow(pid=901, ppid=1, command="node /Users/me/.local/bin/codex"),
            ProcessRow(
                pid=902,
                ppid=901,
                command="/Users/me/.local/share/codex/vendor/aarch64-apple-darwin/codex/codex",
            ),
        ],
        cursor_projects_dir=Path("/does/not/exist"),
    )

    assert [(record.name, record.pid) for record in records] == [("codex", 901)]


def test_discovers_concurrent_codex_sessions_after_deduping_each_pair(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_codex_rollout_owners(monkeypatch, {1002, 1102})

    records = discover_agents(
        process_rows=[
            ProcessRow(pid=1001, ppid=1, command="node /Users/me/.local/bin/codex"),
            ProcessRow(
                pid=1002,
                ppid=1001,
                command="/Users/me/session-a/vendor/aarch64-apple-darwin/codex/codex",
            ),
            ProcessRow(pid=1101, ppid=1, command="node /Users/me/.local/bin/codex"),
            ProcessRow(
                pid=1102,
                ppid=1101,
                command="/Users/me/session-b/vendor/aarch64-apple-darwin/codex/codex",
            ),
        ],
        cursor_projects_dir=Path("/does/not/exist"),
    )

    assert [(record.name, record.pid) for record in records] == [
        ("codex", 1002),
        ("codex", 1102),
    ]


def test_keeps_independent_codex_processes_that_are_not_wrapper_child_pairs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_codex_rollout_owners(monkeypatch, set())

    records = discover_agents(
        process_rows=[
            ProcessRow(pid=1201, ppid=1, command="node /Users/me/.local/bin/codex"),
            ProcessRow(
                pid=1202,
                ppid=1,
                command="/Users/me/.local/share/codex/vendor/aarch64-apple-darwin/codex/codex",
            ),
        ],
        cursor_projects_dir=Path("/does/not/exist"),
    )

    assert [(record.name, record.pid) for record in records] == [
        ("codex", 1201),
        ("codex", 1202),
    ]


def test_registered_records_win_over_discovered_pid(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    registry = AgentRegistry(path=tmp_path / "agents.jsonl")
    registry.register(
        AgentRecord(
            name="manual-claude",
            pid=42,
            command="custom claude wrapper",
            registered_at="2026-05-07T12:00:00+00:00",
        )
    )

    monkeypatch.setattr(
        "app.agents.discovery.discover_agents",
        lambda: [
            AgentRecord(
                name="claude-code",
                pid=42,
                command="claude code",
                source="discovered",
            )
        ],
    )

    records = registered_and_discovered_agents(registry)

    assert len(records) == 1
    assert records[0].name == "manual-claude"
    assert records[0].source == "registered"


def test_registered_and_discovered_agents_returns_sorted_rows(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    registry = AgentRegistry(path=tmp_path / "agents.jsonl")
    registry.register(AgentRecord(name="z-manual", pid=20, command="manual"))

    monkeypatch.setattr(
        "app.agents.discovery.discover_agents",
        lambda: [
            AgentRecord(name="aider", pid=10, command="aider", source="discovered"),
        ],
    )

    records = registered_and_discovered_agents(registry)

    assert [(record.name, record.pid) for record in records] == [("aider", 10), ("z-manual", 20)]
