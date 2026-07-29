"""REPL/gateway ``/remote-sync`` — same shared service as the CLI."""

from __future__ import annotations

import io
from pathlib import Path

import pytest
from rich.console import Console

from platform.filestorage.config import RemoteSyncConfig
from platform.filestorage.engine import SyncReport
from platform.filestorage.enums import SyncRootName
from platform.filestorage.errors import RemoteSyncConfigError
from platform.filestorage.operations import SyncRootStatus, SyncStatus
from surfaces.interactive_shell.command_registry import SLASH_COMMANDS, dispatch_slash
from surfaces.interactive_shell.runtime import Session
from surfaces.interactive_shell.runtime.slash_adapter import headless_slash_ports
from tools.interactive_shell.shared.slash_catalog import MCP_BY_COMMAND


def _capture() -> tuple[Console, io.StringIO]:
    buf = io.StringIO()
    return Console(file=buf, force_terminal=False, highlight=False), buf


def test_remote_sync_registered_in_slash_and_catalog() -> None:
    assert "/remote-sync" in SLASH_COMMANDS
    assert "/remote-sync" in MCP_BY_COMMAND
    assert "/sync" not in SLASH_COMMANDS


def test_gateway_headless_ports_expose_remote_sync() -> None:
    ports = headless_slash_ports()
    assert ports.command_exists("/remote-sync") is True
    assert ports.tty_interactive() is False


def test_status_off_message(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "surfaces.interactive_shell.command_registry.remote_sync_cmds.get_sync_status",
        lambda: SyncStatus(config=None, roots=()),
    )
    console, buf = _capture()
    assert dispatch_slash("/remote-sync status", Session(), console) is True
    assert "Remote sync is off" in buf.getvalue()


def test_status_enabled_shows_roots(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "surfaces.interactive_shell.command_registry.remote_sync_cmds.get_sync_status",
        lambda: SyncStatus(
            config=RemoteSyncConfig(bucket="b", provider="aws", prefix="p"),
            roots=(SyncRootStatus(name=SyncRootName.SESSIONS, path=Path("/s"), exists=True),),
        ),
    )
    console, buf = _capture()
    assert dispatch_slash("/remote-sync", Session(), console) is True
    out = buf.getvalue()
    assert "Remote sync is on (aws)" in out
    assert "b/p" in out
    assert "sessions" in out


def test_sync_subcommand_calls_service(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: dict[str, bool] = {}

    def _run(*, pull_only: bool = False, push_only: bool = False) -> SyncReport:
        seen["pull_only"] = pull_only
        seen["push_only"] = push_only
        return SyncReport(uploaded=["memory/a.md"], skipped=0)

    monkeypatch.setattr(
        "surfaces.interactive_shell.command_registry.remote_sync_cmds.run_remote_sync",
        _run,
    )
    # console.status context manager — Rich Console.status works without a real TTY
    console, buf = _capture()
    assert dispatch_slash("/remote-sync sync --push-only", Session(), console) is True
    assert seen == {"pull_only": False, "push_only": True}
    assert "1 uploaded" in buf.getvalue()


def test_sync_disabled_prints_help(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "surfaces.interactive_shell.command_registry.remote_sync_cmds.run_remote_sync",
        lambda **_kwargs: None,
    )
    console, buf = _capture()
    assert dispatch_slash("/remote-sync sync", Session(), console) is True
    assert "Remote sync is off" in buf.getvalue()


def test_sync_error_is_caught(monkeypatch: pytest.MonkeyPatch) -> None:
    def _boom(**_kwargs: object) -> SyncReport:
        raise RemoteSyncConfigError("bad flags")

    monkeypatch.setattr(
        "surfaces.interactive_shell.command_registry.remote_sync_cmds.run_remote_sync",
        _boom,
    )
    console, buf = _capture()
    assert dispatch_slash("/remote-sync sync --pull-only --push-only", Session(), console) is True
    out = buf.getvalue()
    assert "Sync failed" in out
    # This handler also serves gateway chat, so provider detail must not appear.
    assert "bad flags" not in out, "error detail reached the chat reply"


def test_unknown_subcommand(monkeypatch: pytest.MonkeyPatch) -> None:
    console, buf = _capture()
    assert dispatch_slash("/remote-sync nope", Session(), console) is True
    assert "unknown subcommand" in buf.getvalue()


def test_gateway_dispatch_uses_same_handler(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "surfaces.interactive_shell.command_registry.remote_sync_cmds.get_sync_status",
        lambda: SyncStatus(config=None, roots=()),
    )
    ports = headless_slash_ports()
    console, buf = _capture()
    ok = ports.dispatch(
        "/remote-sync status",
        session=Session(),
        console=console,
        confirm_fn=None,
        is_tty=False,
    )
    assert ok is True
    assert "Remote sync is off" in buf.getvalue()


def test_slash_command_metadata_for_planner() -> None:
    cmd = SLASH_COMMANDS["/remote-sync"]
    assert cmd.first_arg_completions is not None
    labels = {label for label, _hint in cmd.first_arg_completions}
    assert labels == {"status", "sync"}
    assert any("OPENSRE_REMOTE_SYNC" in note for note in (cmd.notes or ()))
    catalog = MCP_BY_COMMAND["/remote-sync"]
    assert "status" in catalog.llm_description
    assert "sync" in catalog.llm_description


def test_sync_shows_kept_remote(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "surfaces.interactive_shell.command_registry.remote_sync_cmds.run_remote_sync",
        lambda **_kwargs: SyncReport(kept_remote=["sessions/newer.jsonl"]),
    )
    console, buf = _capture()
    assert dispatch_slash("/remote-sync sync --push-only", Session(), console) is True
    out = buf.getvalue()
    assert "sessions/newer.jsonl" in out
    assert "newer copy" in out.lower() or "push-only" in out.lower()


def test_help_section_includes_remote_sync() -> None:
    from surfaces.interactive_shell.command_registry.help import _help_sections

    sections = dict(_help_sections())
    assert "/remote-sync" in {c.name for c in sections["Remote sync"]}


def test_repl_and_headless_dispatch_same_command_object() -> None:
    """Gateway headless ports must not register a fork of /remote-sync."""
    from surfaces.interactive_shell.runtime.slash_adapter import repl_slash_ports

    repl = repl_slash_ports()
    headless = headless_slash_ports()
    assert repl.command_exists("/remote-sync")
    assert headless.command_exists("/remote-sync")
    assert SLASH_COMMANDS["/remote-sync"].handler is not None
