"""Tests for slack_capture_task."""

from __future__ import annotations

from integrations.slack.tools.slack_capture_task_tool.tool import (
    append_captured_task,
    slack_capture_task,
)


def test_capture_task_appends(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(
        "integrations.slack.tools.slack_capture_task_tool.tool._store_path",
        lambda: tmp_path / "tasks.jsonl",
    )
    record = append_captured_task(text="fix windows install", requester="U1")
    assert record["text"] == "fix windows install"
    assert (tmp_path / "tasks.jsonl").read_text(encoding="utf-8").count("\n") == 1

    result = slack_capture_task.run(text="ping owners")
    assert result["status"] == "captured"
