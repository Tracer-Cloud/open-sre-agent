"""Tests for ``opensre work`` CLI command validation."""

from __future__ import annotations

from pathlib import Path

import pytest
from click.testing import CliRunner

from config.constants import OPENSRE_WORK_ITEMS_DIR_ENV
from platform.scheduler.store import list_tasks
from surfaces.cli.commands.work import work_command


@pytest.fixture(autouse=True)
def _isolated_stores(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(OPENSRE_WORK_ITEMS_DIR_ENV, str(tmp_path / "work_items"))
    monkeypatch.setattr(
        "platform.scheduler.store._default_store_path",
        lambda: tmp_path / "scheduler_tasks.json",
    )


def test_work_add_accepts_repeated_targets() -> None:
    runner = CliRunner()

    result = runner.invoke(
        work_command,
        [
            "add",
            "Ping every channel",
            "--remind-at",
            "2026-07-30T09:00",
            "--target",
            "slack:C123",
            "--target",
            "telegram:-100",
        ],
    )

    assert result.exit_code == 0
    task = list_tasks()[0]
    assert task.provider.value == "slack"
    assert "telegram" in task.params["delivery_targets"]


def test_work_schedule_checkin_requires_target() -> None:
    runner = CliRunner()

    result = runner.invoke(work_command, ["schedule-checkin", "--cron", "0 9 * * 1-5"])

    assert result.exit_code != 0
    assert "requires --target" in result.output
