"""Tests for agent record storage and JSONL-backed registry."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.agents.registry import AgentRecord, AgentRegistry


@pytest.fixture
def registry(tmp_path: Path) -> AgentRegistry:
    return AgentRegistry(path=tmp_path / "agents.jsonl")


@pytest.fixture
def sample_record() -> AgentRecord:
    return AgentRecord(
        name="claude-code",
        pid=8421,
        command="claude --dangerously-skip-permissions",
        registered_at="2026-05-07T12:00:00+00:00",
    )


class TestAgentRecord:
    def test_frozen(self, sample_record: AgentRecord) -> None:
        with pytest.raises(AttributeError):
            sample_record.name = "aider"  # type: ignore[misc]

    def test_round_trip_dict(self, sample_record: AgentRecord) -> None:
        restored = AgentRecord.from_dict(sample_record.to_dict())
        assert restored == sample_record

    def test_from_dict_missing_registered_at(self) -> None:
        record = AgentRecord.from_dict({"name": "aider", "pid": 1234, "command": "aider"})
        assert record.name == "aider"
        assert record.registered_at  # auto-populated

    def test_from_dict_coerces_types(self) -> None:
        record = AgentRecord.from_dict({"name": "codex", "pid": "9999", "command": "codex"})
        assert record.pid == 9999
        assert isinstance(record.pid, int)


class TestAgentRegistry:
    def test_register_and_list(self, registry: AgentRegistry, sample_record: AgentRecord) -> None:
        registry.register(sample_record)
        records = registry.list()
        assert len(records) == 1
        assert records[0] == sample_record

    def test_register_overwrites_same_pid(
        self, registry: AgentRegistry, sample_record: AgentRecord
    ) -> None:
        registry.register(sample_record)
        updated = AgentRecord(
            name="claude-code-v2",
            pid=8421,
            command="claude",
            registered_at="2026-05-07T13:00:00+00:00",
        )
        registry.register(updated)
        assert len(registry.list()) == 1
        record = registry.get(8421)
        assert record is not None
        assert record.name == "claude-code-v2"

    def test_register_overwrite_deduplicates_disk(
        self, tmp_path: Path, sample_record: AgentRecord
    ) -> None:
        path = tmp_path / "agents.jsonl"
        reg = AgentRegistry(path=path)
        reg.register(sample_record)
        updated = AgentRecord(
            name="claude-code-v2",
            pid=8421,
            command="claude",
            registered_at="2026-05-07T13:00:00+00:00",
        )
        reg.register(updated)

        lines = path.read_text(encoding="utf-8").strip().splitlines()
        assert len(lines) == 1
        assert json.loads(lines[0])["name"] == "claude-code-v2"

    def test_get_returns_none_for_missing(self, registry: AgentRegistry) -> None:
        assert registry.get(99999) is None

    def test_forget_removes_record(
        self, registry: AgentRegistry, sample_record: AgentRecord
    ) -> None:
        registry.register(sample_record)
        removed = registry.forget(8421)
        assert removed == sample_record
        assert registry.list() == []

    def test_forget_missing_returns_none(self, registry: AgentRegistry) -> None:
        assert registry.forget(99999) is None

    def test_clear_empties_registry(
        self, registry: AgentRegistry, sample_record: AgentRecord
    ) -> None:
        registry.register(sample_record)
        registry.register(AgentRecord(name="aider", pid=7702, command="aider"))
        registry.clear()
        assert registry.list() == []

    def test_persistence_round_trip(self, tmp_path: Path, sample_record: AgentRecord) -> None:
        path = tmp_path / "agents.jsonl"
        reg1 = AgentRegistry(path=path)
        reg1.register(sample_record)
        reg1.register(AgentRecord(name="aider", pid=7702, command="aider"))

        reg2 = AgentRegistry(path=path)
        assert len(reg2.list()) == 2
        assert reg2.get(8421) == sample_record

    def test_forget_updates_disk(self, tmp_path: Path, sample_record: AgentRecord) -> None:
        path = tmp_path / "agents.jsonl"
        reg1 = AgentRegistry(path=path)
        reg1.register(sample_record)
        reg1.register(AgentRecord(name="aider", pid=7702, command="aider"))
        reg1.forget(8421)

        reg2 = AgentRegistry(path=path)
        assert len(reg2.list()) == 1
        assert reg2.get(8421) is None
        assert reg2.get(7702) is not None

    def test_corrupt_line_skipped(self, tmp_path: Path) -> None:
        path = tmp_path / "agents.jsonl"
        path.write_text(
            '{"name":"claude-code","pid":8421,"command":"claude","registered_at":"2026-05-07T12:00:00+00:00"}\n'
            "this is not json\n"
            '{"name":"aider","pid":7702,"command":"aider","registered_at":"2026-05-07T12:00:00+00:00"}\n',
            encoding="utf-8",
        )
        reg = AgentRegistry(path=path)
        assert len(reg.list()) == 2

    def test_empty_file(self, tmp_path: Path) -> None:
        path = tmp_path / "agents.jsonl"
        path.write_text("", encoding="utf-8")
        reg = AgentRegistry(path=path)
        assert reg.list() == []

    def test_nonexistent_file(self, tmp_path: Path) -> None:
        path = tmp_path / "does_not_exist.jsonl"
        reg = AgentRegistry(path=path)
        assert reg.list() == []

    def test_jsonl_file_format(self, tmp_path: Path, sample_record: AgentRecord) -> None:
        path = tmp_path / "agents.jsonl"
        reg = AgentRegistry(path=path)
        reg.register(sample_record)

        lines = path.read_text(encoding="utf-8").strip().splitlines()
        assert len(lines) == 1
        parsed = json.loads(lines[0])
        assert parsed["name"] == "claude-code"
        assert parsed["pid"] == 8421
