"""Tests for the interactive-shell CLI assistant.

Covers:

- terminology: the LLM is instructed to call this surface the "interactive
  shell" and is forbidden from using "REPL" in user-facing answers (#604);
- formatting: assistant Markdown output is rendered through Rich's Markdown
  renderer so tables / **bold** / `code` display correctly in the terminal
  instead of leaking raw Markdown syntax (#604).
"""

from __future__ import annotations

import io
from collections.abc import Iterator
from pathlib import Path
from typing import Any

from rich.console import Console

from core.agent_harness.prompts.assistant_agent_prompt import (
    _MARKDOWN_RULE,
    _TERMINOLOGY_RULE,
    _build_observation_block,
    _build_system_prompt,
    build_environment_block,
)
from core.agent_harness.session import ReplSession
from interactive_shell.runtime import agent_harness_adapters as shell_adapters
from interactive_shell.runtime import shell_turn_execution as cli_agent
from interactive_shell.runtime.shell_turn_execution import answer_shell_question


def _build_environment_block(session: ReplSession) -> str:
    """Adapter for the relocated, signature-changed environment-block builder."""
    return build_environment_block(
        integrations=tuple(session.configured_integrations),
        known=session.configured_integrations_known,
    )


def _capture() -> tuple[Console, io.StringIO]:
    buf = io.StringIO()
    # ``force_terminal=True`` so Rich emits its real renderer output (the
    # same path the user sees) rather than collapsing markdown into raw
    # text on a non-tty stream.
    return (
        Console(file=buf, force_terminal=True, color_system=None, width=80, highlight=False),
        buf,
    )


class _FakeLLMClient:
    """Streaming-aware fake.

    ``invoke_stream`` yields the canned content as a single chunk. ``content``
    accepts either a plain string or an Anthropic-style list of content blocks
    (objects with ``.text`` or dicts with a ``"text"`` key); blocks are flattened
    to the same text the real SDK's ``text_stream`` would surface.
    """

    def __init__(self, content: Any) -> None:
        self._content = content
        self.last_prompt: str | None = None

    def invoke_stream(self, prompt: str) -> Iterator[str]:
        self.last_prompt = prompt
        if isinstance(self._content, list):
            parts: list[str] = []
            for block in self._content:
                if isinstance(block, dict):
                    parts.append(block.get("text", ""))
                elif hasattr(block, "text"):
                    parts.append(block.text)
            yield "\n".join(parts)
            return
        yield str(self._content)


def _patch_llm(monkeypatch: Any, content: Any) -> _FakeLLMClient:
    client = _FakeLLMClient(content)
    # ``answer_shell_question`` imports ``get_llm_for_reasoning`` lazily from
    # ``core.llm.llm_client``, so we patch the symbol on that module.
    import core.llm.llm_client as llm_module

    monkeypatch.setattr(llm_module, "get_llm_for_reasoning", lambda: client)
    return client


def _patch_grounding(
    monkeypatch: Any,
    *,
    cli_reference: str = "(ref)",
    agents_md: str = "",
    investigation_flow: str = "",
) -> None:
    """Pin the shell grounding caches the prompt provider reads from.

    The conversational assistant now sources grounding text through
    ``ShellPromptContextProvider`` (over ``session.grounding`` + the
    investigation-flow reference), so tests patch the provider methods rather
    than module-level builders.
    """
    provider = shell_adapters.ShellPromptContextProvider
    monkeypatch.setattr(provider, "cli_reference", lambda _self: cli_reference)
    monkeypatch.setattr(provider, "agents_md", lambda _self: agents_md)
    monkeypatch.setattr(provider, "investigation_flow", lambda _self: investigation_flow)


class TestSystemPromptTerminology:
    """The LLM grounding must steer answers away from the word 'REPL'."""

    def test_conversational_prompt_uses_interactive_shell_not_repl(self) -> None:
        prompt = _build_system_prompt(reference="(ref)", history="(hist)")
        assert "interactive shell" in prompt
        assert "argv" in prompt
        assert "!" in prompt
        # The prompt must explicitly forbid the "REPL" jargon so the model
        # does not echo it back in answers (#604).
        assert _TERMINOLOGY_RULE in prompt
        assert "Never use the word 'REPL'" in prompt

    def test_prompt_requests_markdown_formatting(self) -> None:
        prompt = _build_system_prompt(reference="(ref)", history="(hist)")
        assert _MARKDOWN_RULE in prompt
        assert "Markdown" in prompt

    def test_conversational_prompt_does_not_expose_action_json_contract(self) -> None:
        prompt = _build_system_prompt(reference="(ref)", history="(hist)")

        assert '"action"' not in prompt
        assert "switch_llm_provider" not in prompt
        assert "run_interactive" not in prompt

    def test_prompt_gives_generic_integration_setup_guidance(self) -> None:
        """If a setup request reaches the assistant, it gives guidance only."""
        prompt = _build_system_prompt(reference="(ref)", history="(hist)")
        assert "/integrations setup <service>" in prompt
        assert "/mcp connect <server>" in prompt
        assert "Do not emit JSON" in prompt


class TestSystemPromptAgentsMdGrounding:
    """The conversational shell wires AGENTS.md repo-map content (#1442)."""

    def test_section_present_in_conversational_prompt_when_agents_md_provided(self) -> None:
        prompt = _build_system_prompt(
            reference="(ref)",
            history="(hist)",
            agents_md="repo map content",
        )
        assert "--- Repo map (AGENTS.md) ---" in prompt
        assert "repo map content" in prompt

    def test_section_omitted_when_agents_md_empty(self) -> None:
        prompt = _build_system_prompt(reference="(ref)", history="(hist)", agents_md="")
        assert "--- Repo map (AGENTS.md) ---" not in prompt

    def test_section_omitted_by_default_for_callers_that_dont_pass_it(self) -> None:
        prompt = _build_system_prompt(reference="(ref)", history="(hist)")
        assert "--- Repo map (AGENTS.md) ---" not in prompt


class TestSystemPromptInvestigationFlowGrounding:
    """The conversational shell includes the investigation-flow reference block."""

    def test_investigation_flow_section_present_when_reference_provided(self) -> None:
        prompt = _build_system_prompt(
            reference="(ref)",
            history="(hist)",
            investigation_flow="resolve → extract → investigate → deliver",
        )

        assert "--- Investigation flow reference ---" in prompt
        assert "resolve → extract → investigate → deliver" in prompt
        assert "do not claim the pipeline definition is unavailable" in prompt

    def test_investigation_flow_section_omitted_when_reference_empty(self) -> None:
        prompt = _build_system_prompt(reference="(ref)", history="(hist)", investigation_flow="")

        assert "--- Investigation flow reference ---" not in prompt

    def test_answer_shell_question_injects_investigation_flow_reference(
        self, monkeypatch: Any
    ) -> None:
        client = _patch_llm(monkeypatch, "Yes, I can describe the pipeline.")
        _patch_grounding(
            monkeypatch,
            investigation_flow="resolve → extract → investigate → deliver",
        )

        console, _ = _capture()
        answer_shell_question(
            "Can you see how investigations are structured?", ReplSession(), console
        )

        assert client.last_prompt is not None
        assert "--- Investigation flow reference ---" in client.last_prompt
        assert "resolve → extract → investigate → deliver" in client.last_prompt


class TestEnvironmentIntegrationGrounding:
    """The assistant must be told which integrations are configured (#sentry-context)."""

    def test_block_lists_configured_services_when_known(self) -> None:
        session = ReplSession()
        session.configured_integrations_known = True
        session.configured_integrations = ("gitlab", "datadog")
        block = _build_environment_block(session)
        assert "--- Environment (configured integrations) ---" in block
        assert "gitlab" in block
        assert "datadog" in block
        assert "not in that list is NOT configured" in block

    def test_block_states_none_when_known_and_empty(self) -> None:
        session = ReplSession()
        session.configured_integrations_known = True
        session.configured_integrations = ()
        block = _build_environment_block(session)
        assert "No integrations are configured" in block

    def test_block_omitted_when_unknown(self) -> None:
        session = ReplSession()
        assert session.configured_integrations_known is False
        assert _build_environment_block(session) == ""

    def test_answer_shell_question_injects_configured_integrations(self, monkeypatch: Any) -> None:
        client = _patch_llm(monkeypatch, "No, Sentry is not configured.")
        _patch_grounding(monkeypatch)

        session = ReplSession()
        session.configured_integrations_known = True
        session.configured_integrations = ("gitlab",)
        console, _ = _capture()
        answer_shell_question("is sentry installed?", session, console)

        assert client.last_prompt is not None
        assert "--- Environment (configured integrations) ---" in client.last_prompt
        assert "gitlab" in client.last_prompt


class TestObservationSummaryBlock:
    """The observe→answer loop feeds discovery output back for summarization."""

    def test_block_empty_without_observation(self) -> None:
        assert _build_observation_block(None) == ""
        assert _build_observation_block("   ") == ""

    def test_block_wraps_command_output_with_summarize_instruction(self) -> None:
        block = _build_observation_block("- sentry: missing (Not configured.)")
        assert "tool_results" in block
        assert "- sentry: missing (Not configured.)" in block
        assert "summarize" in block.lower()
        # The summary turn must not kick off more actions.
        assert "not request, plan, or emit any further actions" in block.lower()

    def test_answer_shell_question_injects_observation(self, monkeypatch: Any) -> None:
        client = _patch_llm(monkeypatch, "No — Sentry is not configured.")
        _patch_grounding(monkeypatch)

        session = ReplSession()
        console, _ = _capture()
        observation = (
            "Integration status from `/integrations`:\n- sentry: missing (Not configured.)"
        )
        answer_shell_question(
            "is sentry installed?", session, console, tool_observation=observation
        )

        assert client.last_prompt is not None
        assert "tool_results" in client.last_prompt
        assert "sentry: missing" in client.last_prompt


class TestAssistantOutputRendering:
    """The assistant reply must be rendered, not printed as raw Markdown."""

    def test_bold_markdown_is_rendered(self, monkeypatch: Any) -> None:
        # End-of-stream force-flush renders the buffered text as
        # Markdown — ``**`` delimiters are stripped.
        _patch_llm(monkeypatch, "Hello **world**")
        session = ReplSession()
        console, buf = _capture()
        answer_shell_question("hi", session, console)
        output = _strip_ansi(buf.getvalue())
        assert "**world**" not in output
        assert "world" in output
        assert "Hello" in output
        assert session.token_usage.get("output", 0) > 0

    def test_table_markdown_is_rendered_as_table(self, monkeypatch: Any) -> None:
        markdown = (
            "| Command | What it does |\n|---|---|\n"
            "| `opensre` | Start the interactive shell (TTY) |\n"
        )
        _patch_llm(monkeypatch, markdown)
        session = ReplSession()
        console, buf = _capture()
        answer_shell_question("show commands", session, console)
        output = _strip_ansi(buf.getvalue())
        # Rich's Markdown table renderer replaces the ``|---|---|``
        # separator with box-drawing chars — the literal must not leak.
        assert "|---|---|" not in output
        assert "Command" in output
        assert "What it does" in output
        assert "opensre" in output

    def test_response_is_recorded_in_session_history(self, monkeypatch: Any) -> None:
        _patch_llm(monkeypatch, "Sure thing.")
        session = ReplSession()
        console, _ = _capture()
        answer_shell_question("hello", session, console)
        assert session.cli_agent_messages[-2:] == [
            ("user", "hello"),
            ("assistant", "Sure thing."),
        ]

    def test_command_selection_prompt_uses_llm_response(self, monkeypatch: Any) -> None:
        _patch_llm(monkeypatch, "Use `opensre investigate` for incidents.")
        session = ReplSession()
        console, buf = _capture()
        answer_shell_question("what command do I use?", session, console)
        output = _strip_ansi(buf.getvalue()).casefold()
        assert "opensre investigate" in output
        assert session.cli_agent_messages[-2:] == [
            ("user", "what command do I use?"),
            ("assistant", "Use `opensre investigate` for incidents."),
        ]
        assert session.llm_call_count == 1

    def test_structured_content_blocks_are_rendered(self, monkeypatch: Any) -> None:
        class _Block:
            def __init__(self, text: str) -> None:
                self.text = text

        _patch_llm(monkeypatch, [_Block("First line"), {"text": "Second line"}])
        session = ReplSession()
        console, buf = _capture()
        answer_shell_question("hello", session, console)
        output = _strip_ansi(buf.getvalue())
        assert "First line" in output
        assert "Second line" in output
        assert session.cli_agent_messages[-1] == ("assistant", "First line\nSecond line")

    def test_llm_failure_prints_red_error_and_does_not_record(self, monkeypatch: Any) -> None:
        captured_errors: list[BaseException] = []

        class _Boom:
            def invoke_stream(self, _prompt: str) -> Iterator[str]:
                raise RuntimeError("upstream 503")
                yield  # pragma: no cover  -- generator marker

        import core.llm.llm_client as llm_module

        monkeypatch.setattr(llm_module, "get_llm_for_reasoning", lambda: _Boom())
        monkeypatch.setattr(
            "interactive_shell.utils.error_handling.exception_reporting.capture_exception",
            lambda exc, **_kwargs: captured_errors.append(exc),
        )
        session = ReplSession()
        console, buf = _capture()
        answer_shell_question("hi", session, console)
        output = _strip_ansi(buf.getvalue())
        assert "assistant failed" in output
        assert "upstream 503" in output
        assert len(captured_errors) == 1
        assert isinstance(captured_errors[0], RuntimeError)
        # On failure the turn must NOT be appended to the cli-agent history,
        # otherwise the next turn's prompt would carry a phantom assistant
        # message.
        assert session.cli_agent_messages == []


class TestStreamingMigration:
    """cli_agent must consume invoke_stream and send output through the shared streaming renderer."""

    def test_response_uses_invoke_stream_not_invoke(self, monkeypatch: Any) -> None:
        calls: list[str] = []

        class _Recording:
            def invoke(self, _prompt: str) -> Any:
                calls.append("invoke")
                raise AssertionError("cli_agent must not call invoke after streaming migration")

            def invoke_stream(self, _prompt: str) -> Iterator[str]:
                calls.append("invoke_stream")
                yield "ok"

        import core.llm.llm_client as llm_module

        monkeypatch.setattr(llm_module, "get_llm_for_reasoning", lambda: _Recording())

        console, _ = _capture()
        answer_shell_question("hi", ReplSession(), console)

        assert calls == ["invoke_stream"]

    def test_json_like_response_is_plain_assistant_text(self, monkeypatch: Any) -> None:
        _patch_llm(
            monkeypatch,
            '{"actions":[{"action":"switch_llm_provider","provider":"anthropic"}]}',
        )

        session = ReplSession()
        console, buf = _capture()
        answer_shell_question("switch to anthropic", session, console)

        output = _strip_ansi(buf.getvalue())
        assert '"switch_llm_provider"' in output
        assert "Requested actions" not in output
        assert "$ /model set anthropic" not in output
        assert session.history == []
        assert session.cli_agent_messages[-1] == (
            "assistant",
            '{"actions":[{"action":"switch_llm_provider","provider":"anthropic"}]}',
        )


def test_answer_shell_question_injects_synthetic_observation_on_why_failed(
    tmp_path: Path,
    monkeypatch: Any,
) -> None:
    obs = tmp_path / "latest.json"
    obs.write_text(
        '{"scenario_id": "008-storage-full-missing-metric", "score": {"passed": false}}',
        encoding="utf-8",
    )
    session = ReplSession()
    session.last_synthetic_observation_path = str(obs.resolve())
    console, _buf = _capture()
    client = _patch_llm(monkeypatch, "The synthetic run failed the scoring gate.")
    answer_shell_question("why did it fail?", session, console)
    assert client.last_prompt is not None
    assert "observation_json" in client.last_prompt
    assert "008-storage-full-missing-metric" in client.last_prompt


def test_answer_shell_question_skips_observation_without_failure_question(
    tmp_path: Path,
    monkeypatch: Any,
) -> None:
    obs = tmp_path / "latest.json"
    obs.write_text("{}", encoding="utf-8")
    session = ReplSession()
    session.last_synthetic_observation_path = str(obs.resolve())
    console, _buf = _capture()
    client = _patch_llm(monkeypatch, "hi")
    answer_shell_question("hello", session, console)
    assert client.last_prompt is not None
    assert "observation_json" not in client.last_prompt


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _strip_ansi(text: str) -> str:
    """Remove ANSI escape sequences so assertions test the visible output."""
    import re

    # Standard CSI-sequence regex; covers Rich's bold / color escapes.
    return re.sub(r"\x1b\[[0-9;]*[A-Za-z]", "", text)


def test_module_exports_answer_shell_question() -> None:
    assert "answer_shell_question" in cli_agent.__all__
