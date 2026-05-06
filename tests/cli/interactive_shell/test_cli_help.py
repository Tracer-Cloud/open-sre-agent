"""Tests for the documentation-aware procedural assistant in the interactive shell."""

from __future__ import annotations

import io
import re
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from rich.console import Console

from app.cli.interactive_shell import cli_help, docs_reference
from app.cli.interactive_shell.cli_help import (
    _build_grounded_prompt,
    answer_cli_help,
)
from app.cli.interactive_shell.docs_reference import invalidate_docs_cache
from app.cli.interactive_shell.session import ReplSession


@pytest.fixture(autouse=True)
def _clear_doc_cache() -> Iterator[None]:
    invalidate_docs_cache()
    yield
    invalidate_docs_cache()


def _capture() -> tuple[Console, io.StringIO]:
    buf = io.StringIO()
    return (
        Console(file=buf, force_terminal=True, color_system=None, width=80, highlight=False),
        buf,
    )


class _FakeLLMClient:
    """Streaming-aware fake: ``invoke_stream`` yields the canned content as one chunk."""

    def __init__(self, content: str) -> None:
        self._content = content
        self.last_prompt: str | None = None

    def invoke_stream(self, prompt: str) -> Iterator[str]:
        self.last_prompt = prompt
        yield self._content


def _patch_llm(monkeypatch: pytest.MonkeyPatch, content: str) -> _FakeLLMClient:
    client = _FakeLLMClient(content)
    import app.services.llm_client as llm_module

    monkeypatch.setattr(llm_module, "get_llm_for_reasoning", lambda: client)
    return client


def _seed_docs_root(root: Path) -> None:
    """Build a tiny but representative docs/ tree the test can ground against."""
    root.mkdir(parents=True, exist_ok=True)
    (root / "datadog.mdx").write_text(
        '---\ntitle: "Datadog"\n---\n\n'
        "### Step 1: Create API Key\n\n"
        "Generate an API key under organizational settings.\n",
        encoding="utf-8",
    )
    (root / "deployment.mdx").write_text(
        '---\ntitle: "Deployment"\n---\n\nDeploy OpenSRE to Railway, EC2, or LangGraph Cloud.\n',
        encoding="utf-8",
    )


class TestSystemPromptGrounding:
    def test_prompt_includes_docs_section_when_docs_available(self) -> None:
        prompt = _build_grounded_prompt(
            question="how do I configure Datadog?",
            cli_reference="(cli-ref)",
            docs_reference="(docs-ref)",
        )
        assert "Project documentation" in prompt
        assert "(docs-ref)" in prompt
        assert "(cli-ref)" in prompt
        # Tells the LLM not to invent setup steps that are not in the docs.
        assert "Do NOT invent setup steps" in prompt
        # Question is included in the user block.
        assert "how do I configure Datadog?" in prompt

    def test_prompt_falls_back_when_docs_missing(self) -> None:
        prompt = _build_grounded_prompt(
            question="how do I deploy?",
            cli_reference="(cli-ref)",
            docs_reference="",
        )
        # Falls back to CLI reference + canonical docs URL hint.
        assert "Project documentation is not available" in prompt
        assert "https://www.opensre.com/docs" in prompt
        assert "(cli-ref)" in prompt

    def test_prompt_enforces_terminology_and_markdown_rules(self) -> None:
        prompt = _build_grounded_prompt(
            question="q",
            cli_reference="(cli)",
            docs_reference="(docs)",
        )
        assert "Never use the word 'REPL'" in prompt
        assert "interactive shell" in prompt
        assert "Markdown" in prompt


class TestAnswerCliHelp:
    def test_grounds_prompt_in_relevant_doc_pages(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
    ) -> None:
        _seed_docs_root(tmp_path)
        monkeypatch.setattr(docs_reference, "_DOCS_ROOT", tmp_path)
        client = _patch_llm(monkeypatch, "Configure Datadog with the API key.")

        console, _ = _capture()
        answer_cli_help("how do I configure Datadog?", ReplSession(), console)

        assert client.last_prompt is not None
        # The Datadog page must be inlined into the prompt — that is the whole
        # point of documentation-aware grounding.
        assert "datadog.mdx" in client.last_prompt
        assert "API Key" in client.last_prompt
        # The CLI reference is also included so the LLM can mention commands.
        assert "opensre" in client.last_prompt.lower()

    def test_renders_assistant_markdown(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
    ) -> None:
        _seed_docs_root(tmp_path)
        monkeypatch.setattr(docs_reference, "_DOCS_ROOT", tmp_path)
        _patch_llm(monkeypatch, "Run **opensre investigate** to start.")

        console, buf = _capture()
        answer_cli_help("how do I run an investigation?", ReplSession(), console)

        output = re.sub(r"\x1b\[[0-9;]*[A-Za-z]", "", buf.getvalue())
        # Markdown is rendered: the literal **bold** markers must not leak.
        assert "**opensre investigate**" not in output
        assert "opensre investigate" in output

    def test_handles_missing_docs_gracefully(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
    ) -> None:
        # Point at a directory with NO docs files.
        empty = tmp_path / "no-docs"
        empty.mkdir()
        monkeypatch.setattr(docs_reference, "_DOCS_ROOT", empty)
        client = _patch_llm(monkeypatch, "I cannot find that in the docs.")

        console, _ = _capture()
        answer_cli_help("how do I configure Datadog?", ReplSession(), console)

        assert client.last_prompt is not None
        # When no docs exist, the prompt explicitly states the fallback.
        assert "Project documentation is not available" in client.last_prompt
        # And it must NOT pretend a docs reference was supplied.
        assert "Project documentation" in client.last_prompt
        assert "=== docs/" not in client.last_prompt

    def test_llm_failure_prints_red_error(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        class _Boom:
            def invoke_stream(self, prompt: str) -> Iterator[str]:  # noqa: ARG002
                raise RuntimeError("upstream 503")
                yield  # pragma: no cover  -- generator marker

        import app.services.llm_client as llm_module

        monkeypatch.setattr(llm_module, "get_llm_for_reasoning", lambda: _Boom())

        console, buf = _capture()
        answer_cli_help("how do I deploy?", ReplSession(), console)
        output = re.sub(r"\x1b\[[0-9;]*[A-Za-z]", "", buf.getvalue())
        assert "assistant failed" in output
        assert "upstream 503" in output

    def test_response_uses_invoke_stream_not_invoke(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
    ) -> None:
        """Migration check: cli_help must call invoke_stream, never invoke."""
        _seed_docs_root(tmp_path)
        monkeypatch.setattr(docs_reference, "_DOCS_ROOT", tmp_path)

        calls: list[str] = []

        class _Recording:
            def invoke(self, prompt: str) -> Any:  # noqa: ARG002
                calls.append("invoke")
                raise AssertionError("cli_help must not call invoke after streaming migration")

            def invoke_stream(self, prompt: str) -> Iterator[str]:  # noqa: ARG002
                calls.append("invoke_stream")
                yield "ok"

        import app.services.llm_client as llm_module

        monkeypatch.setattr(llm_module, "get_llm_for_reasoning", lambda: _Recording())

        console, _ = _capture()
        answer_cli_help("how do I configure Datadog?", ReplSession(), console)

        assert calls == ["invoke_stream"]
