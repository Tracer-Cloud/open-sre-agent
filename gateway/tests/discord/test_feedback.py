from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import discord
import pytest

from gateway.transports.discord import feedback
from gateway.transports.discord.feedback import FEEDBACK_GOOD_ID, record_feedback_interaction


def _interaction(custom_id: str) -> SimpleNamespace:
    return SimpleNamespace(
        type=discord.InteractionType.component,
        data={"custom_id": custom_id},
        user=SimpleNamespace(id=42),
        channel_id=123,
        message=SimpleNamespace(id=987),
    )


def test_record_feedback_writes_message_id(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    log_path = tmp_path / "feedback.jsonl"
    monkeypatch.setattr(feedback, "_DEFAULT_FEEDBACK_PATH", log_path)

    assert record_feedback_interaction(_interaction(FEEDBACK_GOOD_ID))  # type: ignore[arg-type]

    entry = json.loads(log_path.read_text(encoding="utf-8").strip())
    assert entry["verdict"] == "good"
    assert entry["message_id"] == "987"
    assert entry["channel_id"] == "123"


def test_record_feedback_ignores_other_custom_ids(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    log_path = tmp_path / "feedback.jsonl"
    monkeypatch.setattr(feedback, "_DEFAULT_FEEDBACK_PATH", log_path)

    assert not record_feedback_interaction(_interaction("something_else"))  # type: ignore[arg-type]
    assert not log_path.exists()
