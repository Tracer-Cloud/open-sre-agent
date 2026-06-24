"""Tests for read-only local agent discovery."""

from __future__ import annotations

from pathlib import Path

import pytest

from app.fleet_monitoring import discovery
from app.fleet_monitoring.discovery import (
    ProcessRow,
    classify_command_provider,
    discover_agents,
    registered_and_discovered_agents,
)
from app.fleet_monitoring.registry import AgentRecord, AgentRegistry


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


def test_discover_agent_processes_all_mode_does_not_mislabel_desktop_as_claude_code(
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
                    "Framework.framework/Helpers/chrome_crashpad_handler"
                ),
            ),
            ProcessRow(
                pid=203,
                command=(
                    "/Applications/Claude.app/Contents/Frameworks/Claude Helper "
                    "(Renderer).app/Contents/MacOS/Claude Helper (Renderer) --type=renderer"
                ),
            ),
        ],
    )

    candidates = discovery.discover_agent_processes(include_all=True)

    assert candidates == []


def test_discover_agent_processes_all_mode_still_keeps_non_desktop_loose_matches(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(discovery.os, "getpid", lambda: 10)
    monkeypatch.setattr(
        discovery,
        "_current_process_rows",
        lambda: [
            ProcessRow(
                pid=311,
                command=(
                    "/Applications/Cursor.app/Contents/Frameworks/"
                    "Cursor Helper (Plugin).app/Contents/MacOS/Cursor Helper (Plugin) "
                    "--cursor-agent-launch"
                ),
            ),
        ],
    )

    candidates = discovery.discover_agent_processes(include_all=True)

    assert [(item.name, item.pid) for item in candidates] == [("cursor-311", 311)]


def test_discover_agent_processes_matches_bare_claude_cli(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(discovery.os, "getpid", lambda: 10)
    monkeypatch.setattr(
        discovery,
        "_current_process_rows",
        lambda: [
            ProcessRow(pid=10, command="opensre"),
            ProcessRow(pid=501, command="claude"),
            ProcessRow(pid=502, command="/Users/me/.npm-global/bin/claude"),
        ],
    )

    candidates = discovery.discover_agent_processes()

    assert [(item.name, item.pid) for item in candidates] == [
        ("claude-code-501", 501),
        ("claude-code-502", 502),
    ]


def test_discover_agent_processes_matches_claude_cli_flag_variants(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(discovery.os, "getpid", lambda: 10)
    monkeypatch.setattr(
        discovery,
        "_current_process_rows",
        lambda: [
            ProcessRow(pid=10, command="opensre"),
            ProcessRow(pid=601, command="claude --resume abc-123"),
            ProcessRow(pid=602, command="claude --prefill 'demo prompt'"),
            ProcessRow(pid=603, command="claude --print 'one shot'"),
            ProcessRow(pid=604, command="claude --continue"),
            ProcessRow(pid=605, command="claude -p 'short flag print'"),
            ProcessRow(pid=606, command="claude -c"),
            ProcessRow(pid=607, command="claude -r abc-123"),
        ],
    )

    candidates = discovery.discover_agent_processes()

    assert [(item.name, item.pid) for item in candidates] == [
        ("claude-code-601", 601),
        ("claude-code-602", 602),
        ("claude-code-603", 603),
        ("claude-code-604", 604),
        ("claude-code-605", 605),
        ("claude-code-606", 606),
        ("claude-code-607", 607),
    ]


def test_discover_agent_processes_matches_claude_cli_equals_form_flags(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(discovery.os, "getpid", lambda: 10)
    monkeypatch.setattr(
        discovery,
        "_current_process_rows",
        lambda: [
            ProcessRow(pid=10, command="opensre"),
            ProcessRow(pid=651, command="claude --resume=abc-123"),
            ProcessRow(pid=652, command="claude --prefill=demo"),
            ProcessRow(pid=653, command="claude --print=one-shot"),
            ProcessRow(pid=654, command="claude -r=abc-123"),
            ProcessRow(pid=655, command="claude -p=short"),
        ],
    )

    candidates = discovery.discover_agent_processes()

    assert [(item.name, item.pid) for item in candidates] == [
        ("claude-code-651", 651),
        ("claude-code-652", 652),
        ("claude-code-653", 653),
        ("claude-code-654", 654),
        ("claude-code-655", 655),
    ]


def test_discover_agent_processes_rejects_claude_desktop_main_in_strict_mode(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(discovery.os, "getpid", lambda: 10)
    monkeypatch.setattr(
        discovery,
        "_current_process_rows",
        lambda: [
            ProcessRow(pid=701, command="/Applications/Claude.app/Contents/MacOS/Claude"),
        ],
    )

    assert discovery.discover_agent_processes() == []


@pytest.mark.parametrize(
    "command",
    [
        "/snap/claude/current/usr/bin/claude",
        "/usr/lib/claude-desktop/claude-desktop",
        "/tmp/.mount_Claude_xyz123/usr/bin/claude",
        "'C:\\Program Files\\Claude\\Claude.exe'",
        "'C:\\Users\\me\\AppData\\Local\\Programs\\Claude\\Claude.exe'",
    ],
)
def test_discover_agent_processes_rejects_claude_desktop_cross_platform_paths(
    monkeypatch: pytest.MonkeyPatch, command: str
) -> None:
    monkeypatch.setattr(discovery.os, "getpid", lambda: 10)
    monkeypatch.setattr(
        discovery,
        "_current_process_rows",
        lambda: [ProcessRow(pid=801, command=command)],
    )

    assert discovery.discover_agent_processes() == []
    assert discovery.discover_agent_processes(include_all=True) == []


def test_discover_agent_processes_does_not_drop_claude_print_with_helper_in_prompt(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(discovery.os, "getpid", lambda: 10)
    monkeypatch.setattr(
        discovery,
        "_current_process_rows",
        lambda: [
            ProcessRow(
                pid=910,
                command="claude --print 'look at the helper output from pty-host yesterday'",
            ),
        ],
    )

    candidates = discovery.discover_agent_processes()

    assert [(item.name, item.pid) for item in candidates] == [("claude-code-910", 910)]


def test_discover_agent_processes_does_not_drop_claude_print_with_desktop_path_in_prompt(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(discovery.os, "getpid", lambda: 10)
    monkeypatch.setattr(
        discovery,
        "_current_process_rows",
        lambda: [
            ProcessRow(
                pid=915,
                command=(
                    "claude --print 'inspect /Applications/Claude.app/Contents/MacOS/Claude logs'"
                ),
            ),
        ],
    )

    candidates = discovery.discover_agent_processes()

    assert [(item.name, item.pid) for item in candidates] == [("claude-code-915", 915)]


@pytest.mark.parametrize(
    "command",
    [
        "/opt/Claude/claude",
        "/opt/Claude/claude --resume id",
        "/opt/claude-cli/bin/claude --resume sess",
    ],
)
def test_discover_agent_processes_allows_cli_installed_under_opt_claude_prefix(
    monkeypatch: pytest.MonkeyPatch, command: str
) -> None:
    monkeypatch.setattr(discovery.os, "getpid", lambda: 10)
    monkeypatch.setattr(
        discovery,
        "_current_process_rows",
        lambda: [ProcessRow(pid=920, command=command)],
    )

    candidates = discovery.discover_agent_processes()

    assert [(item.name, item.pid) for item in candidates] == [("claude-code-920", 920)]


@pytest.mark.parametrize(
    "cmdline",
    [
        ["C:\\Program Files\\Claude\\Claude.exe"],
        ["C:\\Users\\me\\AppData\\Local\\Programs\\Claude\\Claude.exe"],
        ["/Applications/Claude.app/Contents/MacOS/Claude"],
        ["/snap/claude/current/usr/bin/claude"],
        ["/usr/lib/claude-desktop/claude-desktop"],
        ["/tmp/.mount_Claude_xyz123/usr/bin/claude"],
    ],
)
def test_is_claude_desktop_artifact_recognises_known_packaging_paths(
    cmdline: list[str],
) -> None:
    assert discovery._is_claude_desktop_artifact(cmdline) is True


@pytest.mark.parametrize(
    "cmdline",
    [
        ["claude"],
        ["claude", "--resume", "abc"],
        ["/Users/me/.npm-global/bin/claude"],
        ["/Users/me/.local/bin/claude", "--prefill", "demo"],
        ["/usr/local/bin/claude"],
        ["/opt/claude-rs/claude", "--resume", "id"],
        ["/opt/snap/claude-cli/bin/claude"],
        ["/opt/Claude/claude"],
        [
            "claude",
            "--print",
            "inspect /Applications/Claude.app/Contents/MacOS/Claude logs",
        ],
        ["/Users/me/macos/claude"],
        ["/opt/tools/macos/claude"],
        ["/home/user/my-claude-desktop-wrapper/bin/claude"],
        ["/usr/lib/claude-desktop-tools/cli/claude"],
    ],
)
def test_is_claude_desktop_artifact_passes_through_cli_invocations(
    cmdline: list[str],
) -> None:
    assert discovery._is_claude_desktop_artifact(cmdline) is False


def test_discover_agent_processes_rejects_codex_desktop_main_in_strict_mode(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(discovery.os, "getpid", lambda: 10)
    monkeypatch.setattr(
        discovery,
        "_current_process_rows",
        lambda: [
            ProcessRow(pid=801, command="/Applications/Codex.app/Contents/MacOS/Codex"),
        ],
    )

    assert discovery.discover_agent_processes() == []


def test_discover_agent_processes_rejects_cursor_desktop_main_in_strict_mode(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Strict-mode regression guard: no current branch in `_classify_agent`
    # returns "cursor" for `executable == "cursor"`, but the explicit guard
    # next to the `cursor-agent` / `cursor-agent-cli` block must keep
    # Cursor.app from ever being labelled in strict mode.
    monkeypatch.setattr(discovery.os, "getpid", lambda: 10)
    monkeypatch.setattr(
        discovery,
        "_current_process_rows",
        lambda: [
            ProcessRow(pid=804, command="/Applications/Cursor.app/Contents/MacOS/Cursor"),
        ],
    )

    assert discovery.discover_agent_processes() == []


@pytest.mark.parametrize(
    "command",
    [
        "/Applications/Codex.app/Contents/MacOS/Codex",
        "/snap/codex/current/usr/bin/codex",
        "/tmp/.mount_Codex_xyz123/usr/bin/codex",
        "/var/lib/flatpak/app/com.openai.codex/current/active/files/bin/codex",
        "'C:\\Program Files\\Codex\\Codex.exe'",
        "'C:\\Users\\me\\AppData\\Local\\Programs\\codex\\Codex.exe'",
    ],
)
def test_discover_agent_processes_rejects_codex_desktop_cross_platform_paths(
    monkeypatch: pytest.MonkeyPatch, command: str
) -> None:
    monkeypatch.setattr(discovery.os, "getpid", lambda: 10)
    monkeypatch.setattr(
        discovery,
        "_current_process_rows",
        lambda: [ProcessRow(pid=802, command=command)],
    )

    assert discovery.discover_agent_processes() == []
    assert discovery.discover_agent_processes(include_all=True) == []


@pytest.mark.parametrize(
    "command",
    [
        "/Applications/Cursor.app/Contents/MacOS/Cursor",
        "/snap/cursor/current/usr/bin/cursor",
        "/tmp/.mount_Cursor_xyz123/usr/bin/cursor",
        "/var/lib/flatpak/app/com.cursor.cursor/current/active/files/bin/cursor",
        "'C:\\Program Files\\Cursor\\Cursor.exe'",
        "'C:\\Users\\me\\AppData\\Local\\Programs\\cursor\\Cursor.exe'",
    ],
)
def test_discover_agent_processes_rejects_cursor_desktop_cross_platform_paths(
    monkeypatch: pytest.MonkeyPatch, command: str
) -> None:
    monkeypatch.setattr(discovery.os, "getpid", lambda: 10)
    monkeypatch.setattr(
        discovery,
        "_current_process_rows",
        lambda: [ProcessRow(pid=803, command=command)],
    )

    assert discovery.discover_agent_processes() == []
    assert discovery.discover_agent_processes(include_all=True) == []


@pytest.mark.parametrize(
    "cmdline",
    [
        ["/Applications/Codex.app/Contents/MacOS/Codex"],
        ["/snap/codex/current/usr/bin/codex"],
        ["/tmp/.mount_Codex_xyz123/usr/bin/codex"],
        ["/var/lib/flatpak/app/com.openai.codex/current/active/files/bin/codex"],
        ["C:\\Program Files\\Codex\\Codex.exe"],
        ["C:\\Users\\me\\AppData\\Local\\Programs\\codex\\Codex.exe"],
    ],
)
def test_is_codex_desktop_artifact_recognises_known_packaging_paths(
    cmdline: list[str],
) -> None:
    assert discovery._is_codex_desktop_artifact(cmdline) is True


@pytest.mark.parametrize(
    "cmdline",
    [
        ["codex"],
        ["codex", "--resume", "id"],
        ["/usr/local/bin/codex"],
        ["/Users/me/.local/bin/codex"],
        ["/opt/Codex/codex"],
        ["/opt/codex-cli/bin/codex"],
        ["node", "/opt/codex.js"],
        [
            "codex",
            "--print",
            "inspect /Applications/Codex.app/Contents/MacOS/Codex logs",
        ],
        # Electron helper paths inside the bundle stay eligible for the loose
        # matcher — only the main `<bundle>/Contents/MacOS/<binary>` is blocked.
        ["/Applications/Codex.app/Contents/Frameworks/Codex Helper"],
    ],
)
def test_is_codex_desktop_artifact_passes_through_cli_invocations(
    cmdline: list[str],
) -> None:
    assert discovery._is_codex_desktop_artifact(cmdline) is False


@pytest.mark.parametrize(
    "cmdline",
    [
        ["/Applications/Cursor.app/Contents/MacOS/Cursor"],
        ["/snap/cursor/current/usr/bin/cursor"],
        ["/tmp/.mount_Cursor_xyz123/usr/bin/cursor"],
        ["/var/lib/flatpak/app/com.cursor.cursor/current/active/files/bin/cursor"],
        ["C:\\Program Files\\Cursor\\Cursor.exe"],
        ["C:\\Users\\me\\AppData\\Local\\Programs\\cursor\\Cursor.exe"],
    ],
)
def test_is_cursor_desktop_artifact_recognises_known_packaging_paths(
    cmdline: list[str],
) -> None:
    assert discovery._is_cursor_desktop_artifact(cmdline) is True


@pytest.mark.parametrize(
    "cmdline",
    [
        ["cursor-agent"],
        ["cursor-agent-cli"],
        ["/usr/local/bin/cursor-agent"],
        ["/opt/Cursor/cursor"],
        [
            "cursor-agent",
            "--print",
            "/Applications/Cursor.app/Contents/MacOS/Cursor",
        ],
        # Cursor's AI agent ships as a Helper inside the .app bundle. The
        # narrow `cursor.app/contents/macos/cursor/` hint must let helper
        # subprocess paths through so they remain eligible for the loose
        # matcher (see `test_…_all_mode_still_keeps_non_desktop_loose_matches`).
        ["/Applications/Cursor.app/Contents/Frameworks/Cursor Helper (Plugin)"],
    ],
)
def test_is_cursor_desktop_artifact_passes_through_cli_invocations(
    cmdline: list[str],
) -> None:
    assert discovery._is_cursor_desktop_artifact(cmdline) is False


def test_discover_agents_legacy_path_matches_bare_claude_via_cursor_terminal(
    tmp_path: Path,
) -> None:
    terminal = tmp_path / "project" / "terminals" / "72.txt"
    terminal.parent.mkdir(parents=True)
    terminal.write_text(
        "---\npid: 67890\ncwd: /repo\nactive_command: claude --resume sess-1\n---\n",
        encoding="utf-8",
    )

    records = discover_agents(process_rows=[], cursor_projects_dir=tmp_path)

    assert [(record.name, record.pid, record.source) for record in records] == [
        ("claude-code", 67890, "discovered")
    ]


def test_discover_agents_legacy_path_rejects_claude_desktop_main(tmp_path: Path) -> None:
    records = discover_agents(
        process_rows=[
            ProcessRow(pid=901, command="/Applications/Claude.app/Contents/MacOS/Claude"),
        ],
        cursor_projects_dir=tmp_path,
    )

    assert records == []


def test_discover_agents_legacy_path_rejects_codex_desktop_main(tmp_path: Path) -> None:
    records = discover_agents(
        process_rows=[
            ProcessRow(pid=902, command="/Applications/Codex.app/Contents/MacOS/Codex"),
        ],
        cursor_projects_dir=tmp_path,
    )

    assert records == []


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
    # cursor-claude-code wraps the real Claude Code NDJSON output, so the
    # provider rolls up to the same meter (#2023).
    assert records[0].provider == "claude-code"


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
    # cursor-agent-exec emits Cursor-proprietary output that the Anthropic
    # meter cannot parse; rolls up to the ``cursor`` provider so it routes
    # to ``NullMeter`` rather than misreading as claude-code (#2023).
    assert records[0].provider == "cursor"


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
    # ``provider`` is set on cursor-terminal discoveries so the dashboard
    # wiring (#2023) can resolve them without re-classifying.
    assert records[0].provider == "claude-code"


def test_discovered_claude_code_record_carries_provider() -> None:
    records = discover_agents(
        process_rows=[ProcessRow(pid=42, command="claude code")],
        cursor_projects_dir=Path("/does/not/exist"),
    )

    assert records[0].name == "claude-code"
    assert records[0].provider == "claude-code"


def test_discovered_codex_record_carries_provider() -> None:
    records = discover_agents(
        process_rows=[ProcessRow(pid=99, command="codex exec --ephemeral")],
        cursor_projects_dir=Path("/does/not/exist"),
    )

    assert records[0].name == "codex"
    assert records[0].provider == "codex"


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
        "app.fleet_monitoring.discovery.discover_agents",
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
        "app.fleet_monitoring.discovery.discover_agents",
        lambda: [
            AgentRecord(name="aider", pid=10, command="aider", source="discovered"),
        ],
    )

    records = registered_and_discovered_agents(registry)

    assert [(record.name, record.pid) for record in records] == [("aider", 10), ("z-manual", 20)]


class TestClassifyCommandProvider:
    def test_native_codex_binary(self) -> None:
        assert classify_command_provider("/opt/bin/codex exec") == "codex"

    def test_node_launched_codex_js(self) -> None:
        assert (
            classify_command_provider("node /usr/local/bin/codex.js exec --model gpt-5-codex")
            == "codex"
        )

    def test_node_launched_codex_with_intermediate_flag(self) -> None:
        assert classify_command_provider("node --inspect /usr/local/bin/codex.js exec") == "codex"

    def test_node_launched_codex_mjs_and_cjs(self) -> None:
        assert classify_command_provider("node /opt/codex/dist/codex.mjs") == "codex"
        assert classify_command_provider("nodejs /opt/codex/dist/codex.cjs --help") == "codex"

    def test_node_with_unrelated_script_is_not_codex(self) -> None:
        assert classify_command_provider("node /opt/codex-utils/main.js") is None
        assert classify_command_provider("node /opt/app/server.js") is None

    def test_claude_argv0(self) -> None:
        assert classify_command_provider("claude --dangerously-skip-permissions") == "claude-code"

    def test_cursor_extension_substring(self) -> None:
        assert (
            classify_command_provider(
                "/Users/me/.cursor/extensions/anthropic.claude-code/resources/"
                "native-binary/claude --output-format stream-json"
            )
            == "claude-code"
        )

    def test_aider_argv0(self) -> None:
        assert classify_command_provider("aider --model gpt-4o") == "aider"

    def test_gemini_argv0(self) -> None:
        assert classify_command_provider("gemini chat") == "gemini-cli"

    def test_agy_argv0(self) -> None:
        assert classify_command_provider("agy -p hello") == "antigravity-cli"

    def test_unknown_command_returns_none(self) -> None:
        assert classify_command_provider("python -m app.worker") is None

    def test_empty_command_returns_none(self) -> None:
        assert classify_command_provider("") is None

    def test_loose_claude_code_argv_does_not_false_positive(self) -> None:
        # Hardened against the old ``"claude" in tokens and "code" in tokens`` check.
        assert classify_command_provider("run-tests --model claude --format code --verbose") is None
