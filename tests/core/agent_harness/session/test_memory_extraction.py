"""Tests for session-end long-term memory extraction."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest

import core.agent_harness.session.memory_extraction as extraction
from core.domain.memory import list_memories


@pytest.fixture(autouse=True)
def _isolated_memory_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENSRE_MEMORY_DIR", str(tmp_path / "memory"))
    monkeypatch.delenv("OPENSRE_MEMORY_DISABLED", raising=False)
    monkeypatch.delenv("OPENSRE_MEMORY_AUTOEXTRACT_DISABLED", raising=False)


@dataclass
class _FakeSession:
    cli_agent_messages: list[tuple[str, str]] = field(
        default_factory=lambda: [
            ("user", "hi, I'm Vaibhav"),
            ("assistant", "hello!"),
            ("user", "our prod cluster is eks-prod-1"),
            ("assistant", "noted"),
        ]
    )


def _patch_llm(monkeypatch: pytest.MonkeyPatch, response: str) -> list[str]:
    prompts: list[str] = []

    def fake_invoke(messages: list[tuple[str, str]]) -> str:
        prompts.append(str(messages))
        return response

    monkeypatch.setattr(extraction, "_invoke_extraction_llm", fake_invoke)
    return prompts


def _valid_item(name: str = "user-profile") -> dict[str, Any]:
    return {
        "name": name,
        "type": "user",
        "description": "Name is Vaibhav",
        "content": "The user's name is Vaibhav.",
    }


class TestExtraction:
    def test_valid_json_saves_memories(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _patch_llm(monkeypatch, json.dumps([_valid_item()]))
        extraction.extract_memories_from_session(_FakeSession())
        assert [r.slug for r in list_memories()] == ["user-profile"]

    def test_fenced_json_tolerated(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _patch_llm(monkeypatch, f"```json\n{json.dumps([_valid_item()])}\n```")
        extraction.extract_memories_from_session(_FakeSession())
        assert len(list_memories()) == 1

    def test_prose_around_array_tolerated(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _patch_llm(monkeypatch, f"Here you go:\n{json.dumps([_valid_item()])}\nDone.")
        extraction.extract_memories_from_session(_FakeSession())
        assert len(list_memories()) == 1

    @pytest.mark.parametrize("garbage", ["not json", "{}", "[1, 2]", "", '[{"name": 3}]'])
    def test_garbage_saves_nothing_and_does_not_raise(
        self, monkeypatch: pytest.MonkeyPatch, garbage: str
    ) -> None:
        _patch_llm(monkeypatch, garbage)
        extraction.extract_memories_from_session(_FakeSession())
        assert list_memories() == []

    def test_invalid_items_skipped_valid_saved(self, monkeypatch: pytest.MonkeyPatch) -> None:
        items = [
            {"name": "bad", "type": "nonsense", "description": "d", "content": "c"},
            {"name": "no-content", "type": "user", "description": "d", "content": " "},
            _valid_item(),
        ]
        _patch_llm(monkeypatch, json.dumps(items))
        extraction.extract_memories_from_session(_FakeSession())
        assert [r.slug for r in list_memories()] == ["user-profile"]

    def test_cap_of_five_memories(self, monkeypatch: pytest.MonkeyPatch) -> None:
        items = [_valid_item(f"mem-{i}") for i in range(extraction.MAX_MEMORIES_PER_SESSION + 3)]
        _patch_llm(monkeypatch, json.dumps(items))
        extraction.extract_memories_from_session(_FakeSession())
        assert len(list_memories()) == extraction.MAX_MEMORIES_PER_SESSION


class TestSkipConditions:
    def test_skips_short_sessions(self, monkeypatch: pytest.MonkeyPatch) -> None:
        prompts = _patch_llm(monkeypatch, json.dumps([_valid_item()]))
        session = _FakeSession(
            cli_agent_messages=[("user", "hi")] * (extraction.MIN_CHAT_MESSAGES - 1)
        )
        extraction.extract_memories_from_session(session)
        assert prompts == []
        assert list_memories() == []

    def test_skips_when_autoextract_disabled(self, monkeypatch: pytest.MonkeyPatch) -> None:
        prompts = _patch_llm(monkeypatch, json.dumps([_valid_item()]))
        monkeypatch.setenv("OPENSRE_MEMORY_AUTOEXTRACT_DISABLED", "1")
        extraction.extract_memories_from_session(_FakeSession())
        assert prompts == []

    def test_skips_when_memory_disabled(self, monkeypatch: pytest.MonkeyPatch) -> None:
        prompts = _patch_llm(monkeypatch, json.dumps([_valid_item()]))
        monkeypatch.setenv("OPENSRE_MEMORY_DISABLED", "1")
        extraction.extract_memories_from_session(_FakeSession())
        assert prompts == []

    def test_llm_failure_never_raises(self, monkeypatch: pytest.MonkeyPatch) -> None:
        def boom(messages: list[tuple[str, str]]) -> str:
            raise RuntimeError("llm exploded")

        monkeypatch.setattr(extraction, "_invoke_extraction_llm", boom)
        extraction.extract_memories_from_session(_FakeSession())
        assert list_memories() == []

    def test_llm_client_unavailable_returns_empty(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(
            "core.llm.factory.get_llm",
            lambda _role: (_ for _ in ()).throw(RuntimeError("no settings")),
        )
        extraction.extract_memories_from_session(_FakeSession())
        assert list_memories() == []
