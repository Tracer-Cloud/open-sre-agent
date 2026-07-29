"""Tests for durable work-item storage."""

from __future__ import annotations

from pathlib import Path

import pytest

from config.constants import OPENSRE_WORK_ITEMS_DIR_ENV
from core.domain.work_items import (
    WorkItemChannelTarget,
    WorkItemStatus,
    add_work_item,
    complete_work_items,
    list_work_items,
    update_work_item,
    work_items_path,
)


@pytest.fixture(autouse=True)
def _isolated_work_items_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(OPENSRE_WORK_ITEMS_DIR_ENV, str(tmp_path / "work_items"))


def test_add_and_list_open_items() -> None:
    item = add_work_item(title="Finish demo script", priority="high", project="hackathon")

    rows = list_work_items(project="hackathon")

    assert rows == [item]
    assert work_items_path().is_file()


def test_add_stores_multiple_channel_targets() -> None:
    add_work_item(
        title="Ping every channel",
        channel_targets=(
            WorkItemChannelTarget(provider="slack", chat_id="C123"),
            WorkItemChannelTarget(provider="telegram", chat_id="-100"),
        ),
    )

    item = list_work_items()[0]

    assert item.channel.provider == "slack"
    assert [target.provider for target in item.channel_targets] == ["slack", "telegram"]


def test_complete_by_display_id_filters_from_open_list() -> None:
    item = add_work_item(title="Ship reminder flow")

    result = complete_work_items([item.display_id])

    assert result.changed is True
    assert result.completed[0].status is WorkItemStatus.COMPLETED
    assert list_work_items() == []
    assert list_work_items(status=WorkItemStatus.COMPLETED)[0].id == item.id


def test_update_by_title_fragment() -> None:
    add_work_item(title="Write work management docs")

    result = update_work_item("management docs", changes={"priority": "urgent", "owner": "Vaibhav"})

    assert result.changed is True
    assert result.item is not None
    assert result.item.priority.value == "urgent"
    assert result.item.owner == "Vaibhav"


def test_ambiguous_completion_reports_candidates() -> None:
    first = add_work_item(title="Fix Slack reminder")
    second = add_work_item(title="Fix Slack check-in")

    result = complete_work_items(["Fix Slack"])

    assert result.completed == ()
    assert {item.id for item in result.ambiguous["Fix Slack"]} == {first.id, second.id}
