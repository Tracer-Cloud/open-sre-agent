"""CLI adapter for ``opensre remote-sync`` — thin layer over the shared service."""

from __future__ import annotations

from pathlib import Path

import click
import pytest
from click.testing import CliRunner

from config.constants.filestorage import REMOTE_SYNC_BUCKET_ENV, REMOTE_SYNC_ENV
from platform.filestorage.config import RemoteSyncConfig
from platform.filestorage.engine import SyncReport
from platform.filestorage.enums import BuiltInProvider, RemoteSyncSubcommand, SyncRootName
from platform.filestorage.errors import RemoteSyncConfigError
from platform.filestorage.operations import SyncRootStatus, SyncStatus
from surfaces.cli.commands.remote_sync import remote_sync_command


@pytest.fixture
def runner() -> CliRunner:
    return CliRunner()


def test_remote_sync_help_lists_status_and_sync(runner: CliRunner) -> None:
    result = runner.invoke(remote_sync_command, ["--help"])
    assert result.exit_code == 0
    assert RemoteSyncSubcommand.STATUS in result.output
    assert RemoteSyncSubcommand.SYNC in result.output


def test_status_explains_off_when_disabled(
    runner: CliRunner, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from config.constants import paths as paths_mod

    monkeypatch.delenv(REMOTE_SYNC_ENV, raising=False)
    monkeypatch.delenv(REMOTE_SYNC_BUCKET_ENV, raising=False)
    monkeypatch.setattr(paths_mod, "OPENSRE_HOME_DIR", tmp_path)

    result = runner.invoke(remote_sync_command, ["status"])
    assert result.exit_code == 0
    assert "Remote sync is off" in result.output


def test_status_shows_provider_when_enabled(
    runner: CliRunner, monkeypatch: pytest.MonkeyPatch
) -> None:
    status = SyncStatus(
        config=RemoteSyncConfig(bucket="my-bucket", provider=BuiltInProvider.AWS, prefix="opensre"),
        roots=(
            SyncRootStatus(name=SyncRootName.SESSIONS, path=Path("/tmp/sessions"), exists=True),
            SyncRootStatus(name=SyncRootName.MEMORY, path=Path("/tmp/memory"), exists=False),
        ),
    )
    monkeypatch.setattr(
        "surfaces.cli.commands.remote_sync.get_sync_status",
        lambda: status,
    )

    result = runner.invoke(remote_sync_command, ["status"])
    assert result.exit_code == 0
    assert "Remote sync is on (aws)" in result.output
    assert "my-bucket/opensre" in result.output
    assert "sessions" in result.output
    assert "not created yet" in result.output


def test_sync_when_disabled_prints_help(runner: CliRunner, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "surfaces.cli.commands.remote_sync.run_remote_sync",
        lambda **_kwargs: None,
    )
    result = runner.invoke(remote_sync_command, ["sync"])
    assert result.exit_code == 0
    assert "Remote sync is off" in result.output


def test_sync_prints_report(runner: CliRunner, monkeypatch: pytest.MonkeyPatch) -> None:
    report = SyncReport(uploaded=["sessions/a.jsonl"], downloaded=[], skipped=1)
    monkeypatch.setattr(
        "surfaces.cli.commands.remote_sync.run_remote_sync",
        lambda **_kwargs: report,
    )
    result = runner.invoke(remote_sync_command, ["sync"])
    assert result.exit_code == 0
    assert "1 uploaded" in result.output
    assert "already current" in result.output


def test_sync_passes_direction_flags(runner: CliRunner, monkeypatch: pytest.MonkeyPatch) -> None:
    seen: dict[str, bool] = {}

    def _capture(*, pull_only: bool = False, push_only: bool = False) -> SyncReport:
        seen["pull_only"] = pull_only
        seen["push_only"] = push_only
        return SyncReport()

    monkeypatch.setattr(
        "surfaces.cli.commands.remote_sync.run_remote_sync",
        _capture,
    )
    result = runner.invoke(remote_sync_command, ["sync", "--pull-only"])
    assert result.exit_code == 0
    assert seen == {"pull_only": True, "push_only": False}


def test_sync_failure_exits_nonzero(runner: CliRunner, monkeypatch: pytest.MonkeyPatch) -> None:
    def _boom(**_kwargs: object) -> SyncReport:
        raise RemoteSyncConfigError("choose one of --pull-only or --push-only, not both")

    monkeypatch.setattr(
        "surfaces.cli.commands.remote_sync.run_remote_sync",
        _boom,
    )
    result = runner.invoke(remote_sync_command, ["sync", "--pull-only", "--push-only"])
    assert result.exit_code != 0
    assert "Sync failed" in result.output


def test_default_invocation_is_status(
    runner: CliRunner, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from config.constants import paths as paths_mod

    monkeypatch.delenv(REMOTE_SYNC_ENV, raising=False)
    monkeypatch.setattr(paths_mod, "OPENSRE_HOME_DIR", tmp_path)
    result = runner.invoke(remote_sync_command, [])
    assert result.exit_code == 0
    assert "Remote sync is off" in result.output


def test_cli_group_registered_on_main() -> None:
    from surfaces.cli.__main__ import cli

    ctx = click.Context(cli)
    assert "remote-sync" in cli.list_commands(ctx)


def test_top_level_opensre_remote_sync_help(runner: CliRunner) -> None:
    from surfaces.cli.__main__ import cli

    result = runner.invoke(cli, ["remote-sync", "--help"])
    assert result.exit_code == 0
    assert "status" in result.output
    assert "sync" in result.output


def test_status_error_exits_nonzero(runner: CliRunner, monkeypatch: pytest.MonkeyPatch) -> None:
    def _boom() -> SyncStatus:
        raise RemoteSyncConfigError("OPENSRE_REMOTE_SYNC is on but no bucket")

    monkeypatch.setattr(
        "surfaces.cli.commands.remote_sync.get_sync_status",
        _boom,
    )
    result = runner.invoke(remote_sync_command, ["status"])
    assert result.exit_code != 0
    assert "no bucket" in result.output


def test_sync_prints_kept_remote_hint(runner: CliRunner, monkeypatch: pytest.MonkeyPatch) -> None:
    report = SyncReport(
        uploaded=[],
        downloaded=[],
        kept_remote=["sessions/newer.jsonl"],
        skipped=0,
    )
    monkeypatch.setattr(
        "surfaces.cli.commands.remote_sync.run_remote_sync",
        lambda **_kwargs: report,
    )
    result = runner.invoke(remote_sync_command, ["sync", "--push-only"])
    assert result.exit_code == 0
    assert "sessions/newer.jsonl" in result.output
    assert "full sync" in result.output.lower() or "no --push-only" in result.output


def test_sync_help_documents_direction_flags(runner: CliRunner) -> None:
    result = runner.invoke(remote_sync_command, ["sync", "--help"])
    assert result.exit_code == 0
    assert "--pull-only" in result.output
    assert "--push-only" in result.output
