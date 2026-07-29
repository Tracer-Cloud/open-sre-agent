"""Tests for the /work slash commands."""

from __future__ import annotations

import io
from pathlib import Path

import pytest
from rich.console import Console

from config.constants import OPENSRE_WORK_ITEMS_DIR_ENV
from core.domain.work_items import add_work_item, list_work_items
from surfaces.interactive_shell.command_registry import dispatch_slash
from surfaces.interactive_shell.session import Session


@pytest.fixture(autouse=True)
def _isolated_work_items_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(OPENSRE_WORK_ITEMS_DIR_ENV, str(tmp_path / "work_items"))


def _capture() -> tuple[Console, io.StringIO]:
    buf = io.StringIO()
    return Console(file=buf, force_terminal=False, highlight=False), buf


def test_work_lists_open_items() -> None:
    add_work_item(title="Finish demo", priority="high", project="hackathon")
    console, buf = _capture()

    assert dispatch_slash("/work", Session(), console) is True

    assert "Finish demo" in buf.getvalue()


def test_work_add_and_done() -> None:
    console, buf = _capture()

    assert dispatch_slash(
        "/work add Finish slash flow --project hackathon --priority urgent", Session(), console
    )

    item = list_work_items()[0]
    assert item.title == "Finish slash flow"
    assert item.project == "hackathon"

    assert dispatch_slash(f"/work done {item.display_id}", Session(), console)
    assert list_work_items() == []
    assert "completed" in buf.getvalue()


def test_work_next_ranks_items() -> None:
    add_work_item(title="Low polish", priority="low")
    add_work_item(title="Critical blocker", priority="urgent")
    console, buf = _capture()

    assert dispatch_slash("/work next", Session(), console) is True

    output = buf.getvalue()
    assert output.index("Critical blocker") < output.index("Low polish")


def test_work_path_prints_store_path(tmp_path: Path) -> None:
    console, buf = _capture()

    assert dispatch_slash("/work path", Session(), console) is True

    assert str(tmp_path / "work_items" / "work_items.json") in buf.getvalue().replace("\n", "")
