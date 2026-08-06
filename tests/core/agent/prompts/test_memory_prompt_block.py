"""Tests for the long-term memory block in the assistant system prompt."""

from __future__ import annotations

from pathlib import Path

import pytest

from config.constants import OPENSRE_MEMORY_DIR_ENV, OPENSRE_MEMORY_DISABLED_ENV
from core.agent_harness.prompts.assistant import build_assistant_system_prompt
from core.domain.memory import save_memory

_HEADER = "--- Long-term memory ---"


class TestSystemPromptBlock:
    def test_block_rendered_when_memory_text_present(self) -> None:
        prompt = build_assistant_system_prompt(
            "ref",
            "history",
            long_term_memory="- [user] user-profile — Name is Vaibhav (updated 2026-07-09)",
        )
        assert _HEADER in prompt
        assert "user-profile" in prompt
        assert "do not invent details beyond them" in prompt
        assert "do not claim a memory was saved" in prompt

    def test_block_omitted_when_empty(self) -> None:
        prompt = build_assistant_system_prompt("ref", "history", long_term_memory="")
        assert _HEADER not in prompt

    def test_block_omitted_by_default(self) -> None:
        assert _HEADER not in build_assistant_system_prompt("ref", "history")


class TestDefaultProviderMemory:
    @pytest.fixture(autouse=True)
    def _isolated_memory_dir(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv(OPENSRE_MEMORY_DIR_ENV, str(tmp_path / "memory"))
        monkeypatch.delenv(OPENSRE_MEMORY_DISABLED_ENV, raising=False)

    def _provider_memory(self) -> str:
        from core.agent_harness.prompts.grounding import DefaultPromptContextProvider

        return DefaultPromptContextProvider(session=object()).long_term_memory()

    def test_empty_when_no_memories(self) -> None:
        assert self._provider_memory() == ""

    def test_renders_index_when_memories_exist(self) -> None:
        save_memory(
            slug="prod-cluster",
            memory_type="infrastructure",
            description="Prod cluster is eks-prod-1",
            body="eks details live here",
        )
        rendered = self._provider_memory()
        assert "[infrastructure] prod-cluster" in rendered
        assert "eks details live here" in rendered

    def test_empty_when_disabled(self, monkeypatch: pytest.MonkeyPatch) -> None:
        save_memory(
            slug="prod-cluster",
            memory_type="infrastructure",
            description="Prod cluster is eks-prod-1",
            body="details",
        )
        monkeypatch.setenv(OPENSRE_MEMORY_DISABLED_ENV, "1")
        assert self._provider_memory() == ""

    def test_gateway_surface_empty_unless_opted_in(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from config.constants import OPENSRE_MEMORY_GATEWAY_ENABLED_ENV
        from core.agent_harness.prompts.grounding import DefaultPromptContextProvider

        save_memory(
            slug="prod-cluster",
            memory_type="infrastructure",
            description="Prod cluster is eks-prod-1",
            body="details",
        )
        provider = DefaultPromptContextProvider(session=object(), surface="gateway")
        assert provider.long_term_memory() == ""
        monkeypatch.setenv(OPENSRE_MEMORY_GATEWAY_ENABLED_ENV, "1")
        assert "[infrastructure] prod-cluster" in provider.long_term_memory()
